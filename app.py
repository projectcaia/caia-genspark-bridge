# app.py (SendGrid Inbound Parse 전용 - IMAP 제거)
import os
import io
import ssl
import json
import base64
import sqlite3
import datetime as dt
from typing import List, Optional

import requests

from fastapi import FastAPI, Request, UploadFile, Form, File, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr

# --- Optional SDKs ---
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Email, To, Cc, Bcc, Content, Attachment
except Exception:
    SendGridAPIClient = None

APP_VER = "2025-09-16"

app = FastAPI(
    title="Caia Mail Bridge",
    version="2.4.2",
    openapi_version="3.1.0",
    servers=[{"url": "https://mail-bridge.up.railway.app"}],
)

# ===== ENV =====
def env_get(names, default=""):
    for n in names:
        v = os.getenv(n)
        if v not in (None, ""):
            return v
    return default

AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")
INBOUND_TOKEN = os.getenv("INBOUND_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "mailbridge.sqlite3")

SENDER_DEFAULT = os.getenv("SENDER_DEFAULT", "no-reply@example.com")

# Alerts / Noti
ALERT_CLASSES = set([s.strip().upper() for s in os.getenv("ALERT_CLASSES","SENTINEL,REFLEX,ZENSPARK").split(",") if s.strip()])
ALERT_IMPORTANCE_MIN = float(os.getenv("ALERT_IMPORTANCE_MIN", "0.6"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# OpenAI Assistants
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ASSISTANT_ID = os.getenv("ASSISTANT_ID", "")
THREAD_ID = os.getenv("THREAD_ID", "")
AUTO_RUN = os.getenv("AUTO_RUN", "false").lower() in ("1","true","yes")

# SendGrid

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
sg = SendGridAPIClient(api_key=SENDGRID_API_KEY) if (SENDGRID_API_KEY and SendGridAPIClient) else None

# ===== DB =====
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT,
        recipients TEXT,
        subject TEXT,
        text TEXT,
        html TEXT,
        attachments_json TEXT,
        created_at TEXT,
        alert_class TEXT,
        importance REAL
    )
    """)
    conn.commit()
    conn.close()

init_db()

# ===== Helpers =====
def _bearer_from_header(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None

def require_token(token_qs: Optional[str], request: Optional[Request] = None):
    incoming = token_qs or (_bearer_from_header(request) if request else None)
    if not AUTH_TOKEN or incoming != AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")

def telegram_notify(text: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception:
        pass

def simple_alert_parse(subject: Optional[str], text: Optional[str]):
    alert_class = None
    importance = 0.4
    subj = (subject or "").strip()
    if subj.startswith("[") and "]" in subj:
        cls = subj.split("]")[0].strip("[]").upper()
        if cls in ALERT_CLASSES:
            alert_class = cls
            importance = max(importance, 0.6)
    payload = f"{subj}\n{text or ''}".lower()
    for w in ["급락","vix","sev","critical","panic","emergency"]:
        if w in payload:
            importance = min(1.0, importance + 0.15)
    return alert_class, importance


def html_to_text(html: str) -> str:
    """Very light HTML->text fallback: strip tags & unescape basic entities."""
    try:
        import html as _html
        import re as _re
        s = _re.sub(r'<br\s*/?>', '\n', html, flags=_re.I)
        s = _re.sub(r'</p\s*>', '\n', s, flags=_re.I)
        s = _re.sub(r'<[^>]+>', '', s)
        s = _html.unescape(s)
        return s.strip()
    except Exception:
        return (html or '').strip()

def assistants_log_and_maybe_run(sender: str, recipients: str, subject: str, text: str, html: Optional[str]):
    if not (OPENAI_API_KEY and THREAD_ID and OpenAI):
        return {"thread_message_id": None, "run_id": None}
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        content_text = f"From: {sender}\nTo: {recipients}\nSubject: {subject}\n\n{text or ''}"
        msg = client.beta.threads.messages.create(
            thread_id=THREAD_ID,
            role="user",
            content=[{"type":"text","text": content_text}]
        )
        run = None
        if AUTO_RUN and ASSISTANT_ID:
            run = client.beta.threads.runs.create(thread_id=THREAD_ID, assistant_id=ASSISTANT_ID)
        return {"thread_message_id": msg.id, "run_id": getattr(run, "id", None) if run else None}
    except Exception as e:
        print(f"[Assistants ERROR] {e}")
        return {"thread_message_id": None, "run_id": None}

def b64_of_upload(f: UploadFile) -> str:
    data = f.file.read()
    f.file.seek(0)
    return base64.b64encode(data).decode("utf-8")

def parse_to_list(raw) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        items = []
        for x in raw:
            items += [s.strip() for s in str(x).split(",") if s.strip()]
        return items
    return [s.strip() for s in str(raw).split(",") if s.strip()]

# ===== Pydantic =====
class AttachmentInModel(BaseModel):
    filename: str
    content_b64: str
    content_type: Optional[str] = "application/octet-stream"

class SendMailPayload(BaseModel):
    to: List[EmailStr]
    subject: str
    text: Optional[str] = ""
    html: Optional[str] = None
    cc: Optional[List[EmailStr]] = None
    bcc: Optional[List[EmailStr]] = None
    from_: Optional[EmailStr] = None
    reply_to: Optional[EmailStr] = None
    attachments_b64: Optional[List[AttachmentInModel]] = None

class ToolSendReq(BaseModel):
    to: List[EmailStr]
    subject: str
    text: str
    html: Optional[str] = None

class DeleteRequest(BaseModel):
    id: int


# ===== Send Functions =====

def send_via_sendgrid(payload: SendMailPayload) -> int:
    if not sg:
        raise RuntimeError("SendGrid client not configured")

    from_email = Email(str(payload.from_ or SENDER_DEFAULT))
    msg = Mail(from_email=from_email, subject=payload.subject)

    for addr in payload.to:
        msg.add_to(To(str(addr)))

    if payload.cc:
        for addr in payload.cc:
            msg.add_cc(Cc(str(addr)))

    if payload.bcc:
        for addr in payload.bcc:
            msg.add_bcc(Bcc(str(addr)))

    if payload.reply_to:
        msg.reply_to = Email(str(payload.reply_to))

    if payload.text is not None:
        msg.add_content(Content("text/plain", payload.text))

    if payload.html is not None:
        msg.add_content(Content("text/html", payload.html))

    if payload.attachments_b64:
        for att in payload.attachments_b64:
            attachment = Attachment()
            attachment.file_content = att.content_b64
            attachment.file_name = att.filename
            attachment.file_type = att.content_type or "application/octet-stream"
            attachment.disposition = "attachment"
            msg.add_attachment(attachment)

    resp = sg.send(msg)
    return int(resp.status_code)


def send_email(payload: SendMailPayload):
    if not sg:
        raise HTTPException(status_code=500, detail="SendGrid not configured")
    try:
        sc = send_via_sendgrid(payload)
        return {"via": "sendgrid", "status_code": sc}
    except Exception as e:
        print("[SendGrid ERROR]", e)
        raise HTTPException(status_code=502, detail=f"SendGrid send failed: {e}")

# ===== Routes =====

@app.get("/ping")
def ping():
    return {"pong": True}

@app.get("/health")
def health():
    return {
        "ok": True,
        "version": APP_VER,
        "sender": SENDER_DEFAULT,
        
        "sendgrid": bool(sg),
        "inbound": "sendgrid"  # IMAP 제거, SendGrid Inbound Parse 사용
    }

@app.get("/status")
def status(token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    cnt = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    conn.close()
    return {
        "ok": True,
        "version": APP_VER,
        "messages": cnt,
        
        "sendgrid": bool(sg),
        "inbound": "sendgrid"
    }

# === /tool/send - GPT Tool용 간단 발송 ===
@app.post("/tool/send")
def tool_send(payload: ToolSendReq, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    model = SendMailPayload(
        to=payload.to,
        subject=payload.subject,
        text=payload.text,
        html=payload.html,
    )
    res = send_email(model)
    print(f"[TOOL-SEND] via={res['via']} to={','.join(payload.to)} subject={payload.subject}")
    return {"ok": True, **res}

# === Inbound (multipart) ===
@app.post("/inbound")
async def inbound(
    request: Request,
    token: str = Query(..., description="INBOUND_TOKEN"),
    from_field: str = Form(alias="from"),
    to: str = Form(...),
    subject: str = Form(""),
    text: str = Form(""),
    html: Optional[str] = Form(None),
    attachments: Optional[List[UploadFile]] = File(None),
):
    if not INBOUND_TOKEN or token != INBOUND_TOKEN:
        raise HTTPException(status_code=401, detail="invalid inbound token")

    # Fallback: if no text but html exists, generate text from html
    if not text and html:
        text = html_to_text(html)

    atts = []
    if attachments:
        for f in attachments:
            atts.append({
                "filename": f.filename,
                "content_b64": b64_of_upload(f),
                "content_type": f.content_type or "application/octet-stream"
            })

    aclass, importance = simple_alert_parse(subject, text)

    now = dt.datetime.utcnow().isoformat()
    conn = db()
    conn.execute("""
        INSERT INTO messages(sender, recipients, subject, text, html, attachments_json, created_at, alert_class, importance)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (from_field, to, subject, text, html, json.dumps(atts), now, aclass, importance))
    msg_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()

    assist_res = assistants_log_and_maybe_run(from_field, to, subject, text, html)

    if importance >= ALERT_IMPORTANCE_MIN:
        telegram_notify(f"[{aclass or 'INFO'}] {subject}\nfrom {from_field}\n#{msg_id}")

    print(f"[INBOUND] stored id={msg_id} from={from_field}")
    return {"ok": True, "id": msg_id, "assistant": assist_res, "alert_class": aclass, "importance": importance}

# === /inbound/sen - SendGrid Inbound Parse용 ===
@app.post("/inbound/sen")
async def inbound_sen(
    request: Request,
    token: str = Query(...),
    from_field: str = Form(None, alias="from"),
    to: str = Form(None),
    subject: str = Form(""),
    text: str = Form(""),
    html: Optional[str] = Form(None),
    attachments: Optional[List[UploadFile]] = File(None),
):
    if not INBOUND_TOKEN or token != INBOUND_TOKEN:
        raise HTTPException(status_code=401, detail="invalid inbound token")

    from_field = from_field or "unknown@sendgrid.com"
    to = to or SENDER_DEFAULT

    # Fallback: if no text but html exists, generate text from html
    if not text and html:
        text = html_to_text(html)

    atts = []
    if attachments:
        for f in attachments:
            atts.append({
                "filename": f.filename,
                "content_b64": b64_of_upload(f),
                "content_type": f.content_type or "application/octet-stream"
            })

    aclass, importance = simple_alert_parse(subject, text)

    now = dt.datetime.utcnow().isoformat()
    conn = db()
    conn.execute("""
        INSERT INTO messages(sender, recipients, subject, text, html, attachments_json, created_at, alert_class, importance)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (from_field, to, subject, text, html, json.dumps(atts), now, aclass, importance))
    msg_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()

    assist_res = assistants_log_and_maybe_run(from_field, to, subject, text, html)

    if importance >= ALERT_IMPORTANCE_MIN:
        telegram_notify(f"[{aclass or 'INFO'}] {subject}\nfrom {from_field}\n#{msg_id}")

    print(f"[INBOUND-SEN] stored id={msg_id} from={from_field} attachments={len(atts)}")
    return {"ok": True, "id": msg_id, "assistant": assist_res, "alert_class": aclass, "importance": importance}

# === Send ===
@app.post("/mail/send")
def mail_send(payload: SendMailPayload, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    res = send_email(payload)
    print(f"[SEND] via={res['via']} to={','.join(payload.to)} subject={payload.subject}")
    return {"ok": True, **res}

@app.post("/mail/send-multipart")
async def mail_send_multipart(
    token: Optional[str] = Query(None),
    request: Request = None,
    to: List[str] = Form([]),
    subject: str = Form(...),
    text: str = Form(""),
    html: Optional[str] = Form(None),
    cc: Optional[List[str]] = Form(None),
    bcc: Optional[List[str]] = Form(None),
    from_: Optional[str] = Form(None),
    reply_to: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
):
    require_token(token, request)
    to_list = parse_to_list(to)
    cc_list = [EmailStr(x) for x in (parse_to_list(cc) if cc else [])]
    bcc_list = [EmailStr(x) for x in (parse_to_list(bcc) if bcc else [])]

    att_list: List[AttachmentInModel] = []
    if files:
        for f in files:
            att_list.append(AttachmentInModel(
                filename=f.filename,
                content_b64=b64_of_upload(f),
                content_type=f.content_type or "application/octet-stream"
            ))

    model = SendMailPayload(
        to=[EmailStr(x) for x in to_list],
        subject=subject,
        text=text,
        html=html,
        cc=cc_list or None,
        bcc=bcc_list or None,
        from_=EmailStr(from_) if from_ else None,
        reply_to=EmailStr(reply_to) if reply_to else None,
        attachments_b64=att_list or None
    )
    res = send_email(model)
    print(f"[SEND-MP] via={res['via']} to={','.join(to_list)} subject={subject}")
    return {"ok": True, **res}

# === Inbox / View / Attach ===
@app.get("/inbox.json")
def inbox_json(limit: int = 20, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    limit = max(1, min(200, limit))
    conn = db()
    rows = conn.execute("""
        SELECT id, sender, recipients, subject, substr(text,1,500) AS text, created_at,
               CASE WHEN (attachments_json IS NOT NULL AND length(attachments_json) > 2 AND attachments_json != '[]') THEN 1 ELSE 0 END AS has_attachments
        FROM messages ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return {
        "ok": True,
        "messages": [
            {
                "id": r["id"],
                "from": r["sender"],
                "to": r["recipients"],
                "subject": r["subject"],
                "date": r["created_at"],
                "text": r["text"],
                "has_attachments": bool(r["has_attachments"])
            } for r in rows
        ]
    }

@app.get("/mail/view")
def mail_view(id: int = Query(...), token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    row = conn.execute("SELECT * FROM messages WHERE id=?", (id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    files = json.loads(row["attachments_json"] or "[]")
    return {
        "ok": True,
        "message": {
            "id": row["id"],
            "from": row["sender"],
            "to": row["recipients"],
            "subject": row["subject"],
            "date": row["created_at"],
            "text": row["text"],
            "html": row["html"],
            "attachments": files
        }
    }

@app.get("/mail/attach")
def mail_attach(
    id: int = Query(...),
    idx: int = Query(0, ge=0),
    download: int = Query(1, ge=0, le=1),
    token: Optional[str] = Query(None),
    request: Request = None
):
    require_token(token, request)
    conn = db()
    row = conn.execute("SELECT attachments_json FROM messages WHERE id=?", (id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    files = json.loads(row["attachments_json"] or "[]")
    if idx < 0 or idx >= len(files):
        raise HTTPException(status_code=404, detail="attachment not found")
    f = files[idx]
    raw = base64.b64decode(f["content_b64"])
    filename = f.get("filename", f"attach-{id}-{idx}")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return StreamingResponse(io.BytesIO(raw), media_type=f.get("content_type","application/octet-stream"), headers=headers)

@app.post("/mail/delete")
def mail_delete(body: DeleteRequest, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    conn.execute("UPDATE messages SET subject = '[DELETED] ' || subject WHERE id=?", (body.id,))
    conn.commit()
    conn.close()
    print(f"[DELETE] id={body.id}")
    return {"ok": True, "id": body.id}

# === IMAP 대신 SendGrid 사용 안내 ===
@app.post("/mail/poll-now")
def mail_poll_now(token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    # IMAP 폴링 대신 SendGrid Inbound Parse 사용 안내
    print("[POLL-NOW] Using SendGrid Inbound Parse - no polling needed")
    return {
        "ok": True,
        "stored": 0,
        "message": "Using SendGrid Inbound Parse webhook - emails are received automatically"
    }