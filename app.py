# app.py  (FULL REPLACEMENT)
# - 서버 URL 하드코딩 제거(예전 worker-production-4369 제거)
# - Bearer/Query 토큰 동시 허용 (Caia Agent는 Bearer 사용)
# - Zoho 2계정 운용(발신: SMTP_USER=caia@ / 수신: IMAP_USER=axel.nam@)
# - SendGrid 있으면 우선 사용, 실패 시 SMTP 폴백
# - /inbound (multipart) → DB 저장 + (옵션) Assistants Thread 로깅 + 텔레그램 알림
# - /mail/poll-now → Zoho IMAP 폴링 → DB 저장
# - /mail/send-multipart, /mail/delete, /mail/view, /mail/attach, /inbox.json, /status 구현
# - OpenAPI servers 표시는 mail-bridge.up.railway.app 로만 노출

import os
import io
import ssl
import json
import base64
import sqlite3
import datetime as dt
from typing import List, Optional

import requests
import smtplib
import imaplib
import email
from email.message import EmailMessage

from fastapi import FastAPI, Request, UploadFile, Form, File, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr

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

app = FastAPI(
    title="Caia Mail Bridge",
    version="2.4.0",
    openapi_version="3.1.0",
    servers=[{"url": "https://mail-bridge.up.railway.app"}],  # 오픈API 노출용(런타임 동작에는 영향 없음)
)

# ===== ENV =====
def env_get(names, default=""):
    for n in names:
        v = os.getenv(n)
        if v not in (None, ""):
            return v
    return default

AUTH_TOKEN  = os.getenv("AUTH_TOKEN", "")
INBOUND_TOKEN = os.getenv("INBOUND_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "mailbridge.sqlite3")

SENDER_DEFAULT = os.getenv("SENDER_DEFAULT", "no-reply@example.com")

# Alerts / Noti
ALERT_CLASSES = set([s.strip().upper() for s in os.getenv("ALERT_CLASSES","SENTINEL,REFLEX,ZENSPARK").split(",") if s.strip()])
ALERT_IMPORTANCE_MIN = float(os.getenv("ALERT_IMPORTANCE_MIN", "0.6"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# OpenAI Assistants (옵션)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ASSISTANT_ID   = os.getenv("ASSISTANT_ID", "")
THREAD_ID      = os.getenv("THREAD_ID", "")
AUTO_RUN       = os.getenv("AUTO_RUN", "false").lower() in ("1","true","yes")

# SMTP (Zoho 발신: caia@)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.zoho.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = env_get(["ZOHO_SMTP_PASSWORD", "SMTP_PASSWORD"], "")
SMTP_SSL  = os.getenv("SMTP_SSL", "true").lower() in ("1","true","yes")

# IMAP (Zoho 수신: axel.nam@)
IMAP_HOST = os.getenv("IMAP_HOST", "imap.zoho.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASS = env_get(["ZOHO_IMAP_PASSWORD", "IMAP_PASSWORD"], "")
IMAP_SECURE = os.getenv("IMAP_SECURE", "true").lower() in ("1","true","yes")

# SendGrid (옵션)
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
    # Bearer 헤더 또는 query ?token 둘 다 허용
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

def assistants_log_and_maybe_run(sender: str, recipients: str, subject: str, text: str, html: Optional[str]):
    if not (OPENAI_API_KEY and THREAD_ID and OpenAI):
        return {"thread_message_id": None, "run_id": None}
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

def b64_of_upload(f: UploadFile) -> str:
    data = f.file.read()
    f.file.seek(0)
    return base64.b64encode(data).decode("utf-8")

def parse_to_list(raw) -> List[str]:
    # multipart에서 to가 여러 번 오거나 콤마 구분으로 올 수 있으므로 통합 파서
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

# ===== Send (SendGrid → SMTP fallback) =====
def send_via_smtp(payload: SendMailPayload):
    msg = EmailMessage()
    msg["From"] = payload.from_ or SENDER_DEFAULT
    msg["To"] = ", ".join(payload.to)
    msg["Subject"] = payload.subject
    if payload.cc:
        msg["Cc"] = ", ".join(payload.cc)
    if payload.reply_to:
        msg["Reply-To"] = str(payload.reply_to)
    msg.set_content(payload.text or "")
    if payload.html:
        msg.add_alternative(payload.html, subtype="html")
    if payload.attachments_b64:
        for a in payload.attachments_b64:
            raw = base64.b64decode(a.content_b64)
            ctype = a.content_type or "application/octet-stream"
            maintype, subtype = (ctype.split("/", 1) if "/" in ctype else ("application","octet-stream"))
            msg.add_attachment(raw, maintype=maintype, subtype=subtype, filename=a.filename)

    print(f"[SMTP] host={SMTP_HOST} port={SMTP_PORT} ssl={SMTP_SSL} user={SMTP_USER}")
    try:
        if SMTP_SSL:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as s:
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
        print("[SMTP] send OK")
    except Exception as e:
        print("[SMTP ERROR]", e)
        raise

def send_via_sendgrid(payload: SendMailPayload):
    if not sg:
        raise RuntimeError("SendGrid not configured")
    print("[SendGrid] sending via API")
    message = Mail(
        from_email=Email((payload.from_ or SENDER_DEFAULT)),
        to_emails=[To(addr) for addr in payload.to],
        subject=payload.subject,
        plain_text_content=Content("text/plain", payload.text or ""),
        html_content=Content("text/html", payload.html or (payload.text or "")),
    )
    if payload.cc:  message.cc = [Cc(addr) for addr in payload.cc]
    if payload.bcc: message.bcc = [Bcc(addr) for addr in payload.bcc]
    if payload.reply_to: message.reply_to = Email(str(payload.reply_to))
    if payload.attachments_b64:
        att_objs = []
        for a in payload.attachments_b64:
            att = Attachment()
            att.file_content = a.content_b64
            att.file_name = a.filename
            att_objs.append(att)
        message.attachment = att_objs
    resp = sg.send(message)
    print("[SendGrid] status:", getattr(resp, "status_code", None))
    return getattr(resp, "status_code", None)

def send_email(payload: SendMailPayload):
    # SendGrid 우선, 실패하면 SMTP 폴백
    if sg:
        try:
            sc = send_via_sendgrid(payload)
            return {"via": "sendgrid", "status_code": sc}
        except Exception as e:
            print("[SendGrid ERROR] fallback -> SMTP ::", e)
    send_via_smtp(payload)
    return {"via": "smtp", "status_code": 250}

# ===== IMAP Poll =====
def imap_poll(limit: int = 20) -> int:
    print(f"[IMAP] host={IMAP_HOST} port={IMAP_PORT} secure={IMAP_SECURE} user={IMAP_USER}")
    if not IMAP_USER or not IMAP_PASS:
        print("[IMAP] missing credentials")
        return 0
    if IMAP_SECURE:
        m = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    else:
        m = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
    m.login(IMAP_USER, IMAP_PASS)
    m.select("INBOX")
    typ, data = m.search(None, "ALL")
    if typ != "OK":
        m.logout()
        return 0

    ids = data[0].split()
    if not ids:
        m.logout()
        return 0
    ids = ids[-limit:]  # latest N

    stored = 0
    for uid in ids[::-1]:
        typ, msg_data = m.fetch(uid, "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1]
        em = email.message_from_bytes(raw)
        sender = (email.utils.parseaddr(em.get("From"))[1] or "").strip()
        recipients = (em.get("To") or "").strip()
        subject = (em.get("Subject") or "").strip()
        text, html = "", None
        atts = []
        if em.is_multipart():
            for part in em.walk():
                cdispo = (part.get("Content-Disposition") or "").lower()
                ctype = part.get_content_type()
                if cdispo.startswith("attachment"):
                    fn = part.get_filename() or "attachment"
                    payload = part.get_payload(decode=True) or b""
                    atts.append({
                        "filename": fn,
                        "content_b64": base64.b64encode(payload).decode("utf-8"),
                        "content_type": ctype or "application/octet-stream"
                    })
                elif ctype == "text/plain":
                    text += (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", errors="ignore")
                elif ctype == "text/html":
                    html = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", errors="ignore")
        else:
            if (em.get_content_type() or "") == "text/plain":
                text = (em.get_payload(decode=True) or b"").decode(em.get_content_charset() or "utf-8", errors="ignore")

        aclass, importance = simple_alert_parse(subject, text)
        conn = db()
        conn.execute("""
            INSERT INTO messages(sender, recipients, subject, text, html, attachments_json, created_at, alert_class, importance)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (sender, recipients, subject, text, html, json.dumps(atts), dt.datetime.utcnow().isoformat(), aclass, importance))
        conn.commit()
        conn.close()
        stored += 1
    m.logout()
    print(f"[IMAP] stored={stored}")
    return stored

# ===== Routes =====

@app.get("/ping")
def ping():
    return {"pong": True}

@app.get("/health")
def health():
    # 인증 없음 (Caia Agent 초기 헬스체크 용도)
    return {
        "ok": True,
        "version": APP_VER,
        "sender": SENDER_DEFAULT,
        "smtp_user": SMTP_USER,
        "imap_user": IMAP_USER,
        "sendgrid": bool(sg)
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
        "smtp_user": SMTP_USER,
        "imap_user": IMAP_USER,
        "sendgrid": bool(sg),
    }

# === Inbound (SendGrid/n8n → MailBridge) ===
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

    # Assistants Thread 로깅(+자동 실행 옵션)
    assist_res = assistants_log_and_maybe_run(from_field, to, subject, text, html)

    # 중요도 기준 텔레그램 알림
    if importance >= ALERT_IMPORTANCE_MIN:
        telegram_notify(f"[{aclass or 'INFO'}] {subject}\nfrom {from_field}\n#{msg_id}")

    print(f"[INBOUND] stored id={msg_id} from={from_field}")
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
    # 간단 soft-delete: 제목에 [DELETED] 접두 부여
    conn = db()
    conn.execute("UPDATE messages SET subject = '[DELETED] ' || subject WHERE id=?", (body.id,))
    conn.commit()
    conn.close()
    print(f"[DELETE] id={body.id}")
    return {"ok": True, "id": body.id}

@app.post("/mail/poll-now")
def mail_poll_now(token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    stored = imap_poll(limit=20)
    return {"ok": True, "stored": stored}
