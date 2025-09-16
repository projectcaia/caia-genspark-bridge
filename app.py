# app.py (FULL REPLACEMENT) — Zoho SMTP/IMAP logging + SendGrid optional + poll-now + inbound
import os
import io
import json
import base64
import sqlite3
import datetime as dt
from typing import List, Optional

from fastapi import FastAPI, Request, UploadFile, Form, File, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr
import requests

# --- mail + imap/smtp libs ---
import ssl
import smtplib
import imaplib
import email
from email.message import EmailMessage

# --- Optional SDKs ---
try:
    from openai import OpenAI  # pip install openai>=1.40
except Exception:
    OpenAI = None

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Email, To, Cc, Bcc, Content, Attachment
except Exception:
    SendGridAPIClient = None

APP_VER = "2025-09-16"

# === FastAPI (servers 명시: /openapi.json에 포함) ===
app = FastAPI(
    title="Caia Mail Bridge",
    version="2.3.0",
    openapi_version="3.1.0",
    servers=[{"url": "https://mail-bridge.up.railway.app"}],
)

# === ENV ===
def env_get(names, default=""):
    for n in names:
        v = os.getenv(n)
        if v not in (None, ""):
            return v
    return default

# Core
SENDER_DEFAULT = os.getenv("SENDER_DEFAULT", "no-reply@example.com")
INBOUND_TOKEN  = os.getenv("INBOUND_TOKEN", "")
AUTH_TOKEN     = os.getenv("AUTH_TOKEN", "")
DB_PATH        = os.getenv("DB_PATH", "mailbridge.sqlite3")

# Control / Alerts
AUTO_RUN      = os.getenv("AUTO_RUN", "false").lower() == "true"
ALERT_CLASSES = set([s.strip().upper() for s in os.getenv("ALERT_CLASSES","SENTINEL,REFLEX,ZENSPARK").split(",") if s.strip()])
ALERT_IMPORTANCE_MIN = float(os.getenv("ALERT_IMPORTANCE_MIN", "0.6"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "10"))

# OpenAI Assistants (optional)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ASSISTANT_ID   = os.getenv("ASSISTANT_ID", "")
THREAD_ID      = os.getenv("THREAD_ID", "")
client = OpenAI(api_key=OPENAI_API_KEY) if (OPENAI_API_KEY and OpenAI) else None

# Telegram (optional)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# SendGrid (optional send)
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
sg = SendGridAPIClient(api_key=SENDGRID_API_KEY) if (SENDGRID_API_KEY and SendGridAPIClient) else None

# Zoho SMTP/IMAP (main)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.zoho.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = env_get(["ZOHO_SMTP_PASSWORD", "SMTP_PASSWORD"], "")
SMTP_SSL  = os.getenv("SMTP_SSL", "true").lower() in ("1","true","yes")

IMAP_HOST = os.getenv("IMAP_HOST", "imap.zoho.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASS = env_get(["ZOHO_IMAP_PASSWORD", "IMAP_PASSWORD"], "")
IMAP_SECURE = os.getenv("IMAP_SECURE", "true").lower() in ("1","true","yes")

POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "120"))

# === DB ===
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
    )""")
    conn.commit()
    conn.close()

init_db()

# === Helpers ===
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
        raise HTTPException(status_code=401, detail="Unauthorized")

def simple_alert_parse(subject: Optional[str], text: Optional[str]):
    """간단 중요도/클래스 추정: [CLASS] 접두 + 키워드 가중"""
    alert_class = None
    importance = 0.4
    subj = (subject or "").strip()
    if subj.startswith("[") and "]" in subj:
        cls = subj.split("]")[0].strip("[]").upper()
        if cls in ALERT_CLASSES:
            alert_class = cls
            importance = max(importance, 0.6)
    payload = f"{subj}\n{text or ''}".lower()
    hot_words = ["급락", "vix", "covix", "panic", "sev", "critical", "emergency"]
    bumps = sum(1 for w in hot_words if w in payload)
    importance = min(1.0, importance + bumps*0.15)
    return alert_class, importance

def telegram_notify(text: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=HTTP_TIMEOUT)
    except Exception:
        pass

def assistants_log_and_maybe_run(sender: str, recipients: str, subject: str, text_: str, html: Optional[str]):
    if not (client and THREAD_ID):
        return {"thread_message_id": None, "run_id": None}
    try:
        content_text = f"From: {sender}\nTo: {recipients}\nSubject: {subject}\n\n{text_ or ''}"
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
        print("[Assistants ERROR]", e)
        return {"thread_message_id": None, "run_id": None}

def b64_of_upload(f: UploadFile) -> str:
    data = f.file.read()
    f.file.seek(0)
    return base64.b64encode(data).decode("utf-8")

# === Models ===
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
    """툴에서 쓰기 쉬운 최소 필드 모델"""
    to: List[EmailStr]
    subject: str
    text: str
    html: Optional[str] = None

# === Send (Zoho SMTP) ===
def send_via_smtp(payload: SendMailPayload):
    msg = EmailMessage()
    sender = payload.from_ or SENDER_DEFAULT
    msg["From"] = sender
    msg["To"] = ", ".join(payload.to)
    msg["Subject"] = payload.subject
    if payload.cc:
        msg["Cc"] = ", ".join(payload.cc)
    if payload.reply_to:
        msg["Reply-To"] = payload.reply_to

    # body
    msg.set_content(payload.text or "")
    if payload.html:
        msg.add_alternative(payload.html, subtype="html")

    # attachments
    if payload.attachments_b64:
        for a in (payload.attachments_b64 or []):
            raw = base64.b64decode(a.content_b64)
            ctype = a.content_type or "application/octet-stream"
            maintype, subtype = (ctype.split("/", 1) if "/" in ctype else ("application","octet-stream"))
            msg.add_attachment(raw, maintype=maintype, subtype=subtype, filename=a.filename)

    try:
        print("[SMTP] connecting to", SMTP_HOST, SMTP_PORT, "SSL=", SMTP_SSL)
        if SMTP_SSL:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as s:
                print("[SMTP] connected, login:", SMTP_USER)
                s.login(SMTP_USER, SMTP_PASS)
                print("[SMTP] login OK, sending...")
                s.send_message(msg)
                print("[SMTP] send OK")
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
                s.ehlo(); s.starttls()
                print("[SMTP] connected (TLS), login:", SMTP_USER)
                s.login(SMTP_USER, SMTP_PASS)
                print("[SMTP] login OK, sending...")
                s.send_message(msg)
                print("[SMTP] send OK")
    except Exception as e:
        print("[SMTP ERROR]", e)
        raise

# === Send (SendGrid) ===
def send_via_sendgrid(payload: SendMailPayload):
    print("[SendGrid] using API key for send")
    message = Mail(
        from_email=Email((payload.from_ or SENDER_DEFAULT)),
        to_emails=[To(addr) for addr in payload.to],
        subject=payload.subject,
        plain_text_content=Content("text/plain", payload.text or ""),
        html_content=Content("text/html", payload.html or (payload.text or "")),
    )
    if payload.cc:
        message.cc = [Cc(addr) for addr in payload.cc]
    if payload.bcc:
        message.bcc = [Bcc(addr) for addr in payload.bcc]
    if payload.reply_to:
        message.reply_to = Email(str(payload.reply_to))
    if payload.attachments_b64:
        att_objs = []
        for a in payload.attachments_b64:
            att = Attachment()
            att.file_content = a.content_b64
            att.file_name = a.filename
            # (SendGrid는 content_type 없이도 동작하지만 필요 시 a.content_type 사용)
            att_objs.append(att)
        message.attachment = att_objs
    resp = sg.send(message)
    print("[SendGrid] status_code:", getattr(resp, "status_code", None))
    return getattr(resp, "status_code", None)

def send_email(payload: SendMailPayload):
    # SendGrid가 설정되어 있으면 SendGrid 우선, 아니면 Zoho SMTP
    if sg:
        try:
            sc = send_via_sendgrid(payload)
            return {"ok": True, "via": "sendgrid", "status_code": sc}
        except Exception as e:
            print("[SendGrid ERROR]", e, "-> fallback to SMTP")
            # 실패 시 SMTP로 폴백
    # SMTP path
    send_via_smtp(payload)
    return {"ok": True, "via": "smtp"}

# === IMAP Poll (Zoho) ===
def _store_message(sender, recipients, subject, text_, html, atts):
    aclass, importance = simple_alert_parse(subject, text_)
    now = dt.datetime.utcnow().isoformat()
    conn = db()
    conn.execute("""
        INSERT INTO messages(sender, recipients, subject, text, html, attachments_json, created_at, alert_class, importance)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (sender, recipients, subject, text_, html, json.dumps(atts or []), now, aclass, importance))
    msg_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()
    if importance >= ALERT_IMPORTANCE_MIN:
        telegram_notify(f"[{aclass or 'INFO'}] {subject}\nfrom {sender}\n#{msg_id}")
    assistants_log_and_maybe_run(sender, recipients, subject, text_, html)
    return msg_id

def _parts_to_texts(msg):
    text, html = None, None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    text = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    text = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
            elif ctype == "text/html" and "attachment" not in disp:
                try:
                    html = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    html = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
    else:
        ctype = msg.get_content_type()
        if ctype == "text/plain":
            text = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace")
        elif ctype == "text/html":
            html = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace")
    return text, html

def _extract_attachments(msg):
    atts = []
    for part in msg.walk():
        disp = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disp:
            filename = part.get_filename() or "attachment.bin"
            raw = part.get_payload(decode=True) or b""
            ctype = part.get_content_type() or "application/octet-stream"
            atts.append({
                "filename": filename,
                "content_b64": base64.b64encode(raw).decode("utf-8"),
                "content_type": ctype
            })
    return atts

async def imap_poll_once() -> int:
    if not IMAP_SECURE:
        print("[IMAP] non-secure mode not supported; set IMAP_SECURE=true")
    print("[IMAP] connecting to", IMAP_HOST, IMAP_PORT)
    try:
        M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) if IMAP_SECURE else imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        M.login(IMAP_USER, IMAP_PASS)
        print("[IMAP] login OK:", IMAP_USER)
    except Exception as e:
        print("[IMAP ERROR] login failed:", e)
        try:
            M.logout()
        except Exception:
            pass
        return 0

    try:
        typ, _ = M.select("INBOX")
        print("[IMAP] select INBOX:", typ)
        # UNSEEN 우선, 없으면 최근 20개
        typ, data = M.search(None, "UNSEEN")
        ids = (data[0].split() if (typ == "OK" and data and data[0]) else [])
        if not ids:
            typ, data = M.search(None, "ALL")
            all_ids = (data[0].split() if (typ == "OK" and data and data[0]) else [])
            ids = all_ids[-20:] if all_ids else []
        print(f"[IMAP] found {len(ids)} message(s) to process")

        count = 0
        for num in ids:
            typ, msg_data = M.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw_msg = msg_data[0][1]
            em = email.message_from_bytes(raw_msg)
            sender = (email.utils.parseaddr(em.get("From"))[1] or "")
            recipients = (em.get("To") or "")
            subject = (em.get("Subject") or "")
            text, html = _parts_to_texts(em)
            atts = _extract_attachments(em)
            _store_message(sender, recipients, subject, text, html, atts)
            # 읽음 표시
            try:
                M.store(num, '+FLAGS', '\\Seen')
            except Exception:
                pass
            count += 1

        M.close()
        M.logout()
        print("[IMAP] done, stored:", count)
        return count
    except Exception as e:
        print("[IMAP ERROR]", e)
        try:
            M.logout()
        except Exception:
            pass
        return 0

# === Routes ===
@app.get("/ping")
def ping():
    return {"pong": True}

@app.get("/health")
def health():
    # 구성 상태도 같이 보여줌
    return {
        "ok": True,
        "sender": SENDER_DEFAULT,
        "smtp_user": SMTP_USER,
        "imap_user": IMAP_USER,
        "sendgrid": bool(sg),
        "version": APP_VER
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
        "db_path": DB_PATH,
        "messages": cnt,
        "auto_run": AUTO_RUN,
        "alert_classes": list(ALERT_CLASSES),
    }

# --- Inbound (SendGrid/n8n 등) — /inbound + /inbound/sen 호환 ---
async def _handle_inbound_common(token: str, from_: str, to: str, subject: str, text_: str, html: Optional[str], attachments: Optional[List[UploadFile]]):
    if not INBOUND_TOKEN or token != INBOUND_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")
    atts = []
    if attachments:
        for f in attachments:
            atts.append({
                "filename": f.filename,
                "content_b64": b64_of_upload(f),
                "content_type": f.content_type or "application/octet-stream"
            })
    aclass, importance = simple_alert_parse(subject, text_)
    msg_id = _store_message(from_, to, subject, text_, html, atts)
    if importance >= ALERT_IMPORTANCE_MIN:
        telegram_notify(f"[{aclass or 'INFO'}] {subject}\nfrom {from_}\n#{msg_id}")
    return {"ok": True, "id": msg_id, "alert_class": aclass, "importance": importance}

@app.post("/inbound")
async def inbound(
    request: Request,
    token: str = Query(..., description="INBOUND_TOKEN"),
    from_: str = Form(alias="from"),
    to: str = Form(...),
    subject: str = Form(""),
    text: str = Form(""),
    html: Optional[str] = Form(None),
    attachments: Optional[List[UploadFile]] = File(None),
):
    return await _handle_inbound_common(token, from_, to, subject, text, html, attachments)

@app.post("/inbound/sen")
async def inbound_sen(
    request: Request,
    token: str = Query(..., description="INBOUND_TOKEN"),
    from_: str = Form(alias="from"),
    to: str = Form(...),
    subject: str = Form(""),
    text: str = Form(""),
    html: Optional[str] = Form(None),
    attachments: Optional[List[UploadFile]] = File(None),
):
    return await _handle_inbound_common(token, from_, to, subject, text, html, attachments)

# --- Inbox/List ---
@app.post("/mail/inbox")
def inbox_post(limit: int = 10, subject: Optional[str] = None, sender: Optional[str] = None, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    limit = max(1, min(200, limit))
    where = []
    args = []
    if subject:
        where.append("subject LIKE ?")
        args.append(f"%{subject}%")
    if sender:
        where.append("sender LIKE ?")
        args.append(f"%{sender}%")
    wh = ("WHERE " + " AND ".join(where)) if where else ""
    conn = db()
    rows = conn.execute(f"SELECT id, sender, recipients, subject, substr(text,1,400) AS text, created_at FROM messages {wh} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    conn.close()
    return {"ok": True, "emails": [dict(r) for r in rows]}

@app.get("/inbox.json")
def inbox_json(limit: int = 10, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    limit = max(1, min(200, limit))
    conn = db()
    rows = conn.execute("""
        SELECT id, sender, recipients, subject, substr(text,1,500) AS text, created_at, alert_class, importance,
               CASE WHEN (attachments_json IS NOT NULL AND length(attachments_json) > 2 AND attachments_json != '[]') THEN 1 ELSE 0 END AS has_attachments
        FROM messages ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return {"ok": True, "messages": [
        {
            "id": r["id"],
            "from": r["sender"],
            "to": r["recipients"],
            "subject": r["subject"],
            "date": r["created_at"],
            "text": r["text"],
            "has_attachments": bool(r["has_attachments"])
        } for r in rows
    ]}

@app.get("/mail/view")
def mail_view(id: int = Query(...), token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    row = conn.execute("SELECT * FROM messages WHERE id=?", (id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    data = dict(row)
    data["attachments"] = json.loads(data.pop("attachments_json") or "[]")
    return {"ok": True, "message": {
        "id": data["id"],
        "from": data["sender"],
        "to": data["recipients"],
        "subject": data["subject"],
        "date": data["created_at"],
        "text": data["text"],
        "html": data["html"],
        "attachments": data["attachments"],
    ]}

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

# --- Send (JSON body) ---
@app.post("/mail/send")
def mail_send(payload: SendMailPayload, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    res = send_email(payload)
    return {"ok": True, **res}

# --- Send (multipart) ---
@app.post("/mail/send-multipart")
async def mail_send_multipart(
    token: Optional[str] = Query(None),
    request: Request = None,
    to: List[str] = Form(...),
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
    atts = []
    if files:
        for f in files:
            atts.append(AttachmentInModel(
                filename=f.filename,
                content_b64=b64_of_upload(f),
                content_type=f.content_type or "application/octet-stream"
            ))
    payload = SendMailPayload(
        to=to, subject=subject, text=text, html=html,
        cc=cc, bcc=bcc, from_=from_, reply_to=reply_to,
        attachments_b64=atts or None
    )
    res = send_email(payload)
    return {"ok": True, **res}

# --- Poll IMAP now ---
@app.post("/mail/poll-now")
async def poll_now(token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    count = await imap_poll_once()
    return {"ok": True, "stored": count}

# --- Tool-friendly wrapper ---
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
    return {"ok": True, **res}

# --- Simple digest to Telegram ---
@app.post("/mail/digest")
def inbox_digest(limit: int = 10, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    limit = max(1, min(200, limit))
    conn = db()
    rows = conn.execute("SELECT id, sender, subject, substr(text,1,200) AS text, created_at FROM messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    if not rows:
        telegram_notify("📭 최근 수신 메일 없음")
        return {"ok": True, "lines": 0}
    lines = [f"- [{r['created_at']}] {r['sender']} / {r['subject']} :: {r['text']}" for r in rows]
    msg = "📬 최근 메일 요약\n" + "\n".join(lines)
    telegram_notify(msg)
    return {"ok": True, "lines": len(lines)}
