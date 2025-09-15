# app.py — Caia Mail Bridge (Rebuilt 2025-09-15)
import os, io, json, base64, asyncio, sqlite3, datetime as dt, traceback
from typing import List, Optional, Dict, Any
from email import message_from_bytes
from email.message import EmailMessage

import requests
from fastapi import FastAPI, Request, UploadFile, File, Form, Query, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, EmailStr

# ---------- OpenAPI server base from MAIL_BASE ----------
MAIL_BASE = os.getenv("MAIL_BASE")
servers = [{"url": MAIL_BASE}] if MAIL_BASE else []

app = FastAPI(
    title="Caia Mail Bridge",
    version="2.0.0",
    openapi_version="3.1.0",
    servers=servers
)

# ---------- ENV ----------
SENDER_DEFAULT = os.getenv("SENDER_DEFAULT", "no-reply@example.com")
INBOUND_TOKEN  = os.getenv("INBOUND_TOKEN", "")
AUTH_TOKEN     = os.getenv("AUTH_TOKEN", "")

DB_PATH        = os.getenv("DB_PATH", "mailbridge.sqlite3")
DATA_DIR       = os.getenv("DATA_DIR", "data")
ATTACH_DIR     = os.path.join(DATA_DIR, "attachments")

# IMAP (Zoho)
IMAP_HOST = os.getenv("IMAP_HOST", "")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993") or "993")
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASS = os.getenv("ZOHO_IMAP_PASSWORD", os.getenv("IMAP_PASSWORD", ""))
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")

# SMTP (Zoho) or SendGrid
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465") or "465")
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("ZOHO_SMTP_PASSWORD", os.getenv("SMTP_PASSWORD",""))
SMTP_SSL  = os.getenv("SMTP_SSL", "true").lower() in ("1","true","yes")

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")

# Optional alerts
ALERT_CLASSES = set([s.strip().upper() for s in os.getenv("ALERT_CLASSES","SENTINEL,REFLEX,ZENSPARK").split(",") if s.strip()])
ALERT_IMPORTANCE_MIN = float(os.getenv("ALERT_IMPORTANCE_MIN", "0.6"))
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "120") or "120")
AUTO_RUN = os.getenv("AUTO_RUN", "false").lower() in ("1","true","yes")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ---------- DB ----------
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        imap_uid INTEGER,
        message_id TEXT,
        sender TEXT,
        recipients TEXT,
        subject TEXT,
        text TEXT,
        html TEXT,
        attachments_json TEXT,
        created_at TEXT,
        alert_class TEXT,
        importance REAL,
        deleted INTEGER DEFAULT 0
    )
    """)
    # metadata for poller
    cur.execute("""
    CREATE TABLE IF NOT EXISTS meta(
        k TEXT PRIMARY KEY,
        v TEXT
    )
    """)
    conn.commit()
    conn.close()
init_db()

def set_meta(k: str, v: str):
    conn = db()
    conn.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k,v))
    conn.commit(); conn.close()

def get_meta(k: str, default: Optional[str]=None) -> Optional[str]:
    conn = db()
    row = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    conn.close()
    return row["v"] if row else default

# ---------- Helpers ----------
def bearer_from_header(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split()
    return parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else None

def require_token(token_qs: Optional[str], request: Optional[Request] = None):
    incoming = token_qs or (bearer_from_header(request) if request else None)
    if not AUTH_TOKEN or incoming != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

def b64_of_upload(f: UploadFile) -> str:
    raw = f.file.read()
    return base64.b64encode(raw).decode()

def save_attachment_bytes(msg_key: str, filename: str, content: bytes) -> str:
    safe_name = filename or "attach.bin"
    folder = os.path.join(ATTACH_DIR, msg_key)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, safe_name)
    with open(path, "wb") as wf:
        wf.write(content)
    return path

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
    hot_words = ["급락", "vix", "covix", "panic", "sev", "critical", "emergency"]
    bumps = sum(1 for w in hot_words if w in payload)
    importance = min(1.0, importance + bumps*0.15)
    return alert_class, importance

def notify_caia(event: Dict[str, Any]):
    # Optional webhook to Caia Agent/Hubs (if MAIL_BASE set)
    if not MAIL_BASE:
        return
    try:
        headers = {"Authorization": f"Bearer {AUTH_TOKEN}"} if AUTH_TOKEN else {}
        requests.post(f"{MAIL_BASE.rstrip('/')}/events/mail", json=event, headers=headers, timeout=5)
    except Exception:
        pass

def tg_notify(text: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=5)
    except Exception:
        pass

# ---------- Models ----------
class AttachmentIn(BaseModel):
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
    attachments_b64: Optional[List[AttachmentIn]] = None

# ---------- Email sending ----------
def send_via_smtp(payload: SendMailPayload):
    import smtplib, ssl
    msg = EmailMessage()
    sender = payload.from_ or SENDER_DEFAULT
    msg["From"] = sender
    msg["To"] = ", ".join(payload.to)
    msg["Subject"] = payload.subject
    if payload.cc: msg["Cc"] = ", ".join(payload.cc)
    if payload.reply_to: msg["Reply-To"] = payload.reply_to
    msg.set_content(payload.text or "")
    if payload.html:
        msg.add_alternative(payload.html, subtype="html")
    if payload.attachments_b64:
        for a in payload.attachments_b64 or []:
            raw = base64.b64decode(a.content_b64)
            maintype, subtype = (a.content_type or "application/octet-stream").split("/",1)
            msg.add_attachment(raw, maintype=maintype, subtype=subtype, filename=a.filename)

    if SMTP_SSL:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as s:
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.ehlo(); s.starttls()
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)

def send_via_sendgrid(payload: SendMailPayload):
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Email, To, Cc, Bcc, Content, Attachment
    sender = str(payload.from_ or SENDER_DEFAULT)
    message = Mail(
        from_email=Email(sender),
        to_emails=[To(str(x)) for x in payload.to],
        subject=payload.subject,
        html_content=Content("text/html", payload.html or (payload.text or "")),
    )
    if payload.cc:
        message.cc = [Cc(str(x)) for x in payload.cc]
    if payload.bcc:
        message.bcc = [Bcc(str(x)) for x in payload.bcc]
    if payload.attachments_b64:
        att_objs = []
        for a in payload.attachments_b64 or []:
            att = Attachment()
            att.file_content = a.content_b64
            att.file_name = a.filename
            att.file_type = a.content_type or "application/octet-stream"
            att_objs.append(att)
        message.attachment = att_objs
    sg = SendGridAPIClient(SENDGRID_API_KEY)
    sg.send(message)

def send_email(payload: SendMailPayload):
    if SENDGRID_API_KEY:
        return send_via_sendgrid(payload)
    return send_via_smtp(payload)

# ---------- IMAP Poller ----------
async def imap_poll_once() -> int:
    """
    Returns number of new messages saved.
    """
    import imaplib, email
    if not (IMAP_HOST and IMAP_USER and IMAP_PASS):
        return 0
    last_uid = int(get_meta("last_uid", "0") or "0")
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    M.login(IMAP_USER, IMAP_PASS)
    M.select(IMAP_FOLDER)
    # Fetch new UIDs greater than last_uid
    typ, data = M.uid("search", None, f"(UID {last_uid+1}:*)")
    if typ != "OK":
        M.logout()
        return 0
    uids = [int(x) for x in (data[0] or b"").split()]
    new_count = 0
    for uid in uids:
        typ, msgdata = M.uid("fetch", str(uid), "(RFC822)")
        if typ != "OK": 
            continue
        raw = msgdata[0][1]
        em = email.message_from_bytes(raw)
        frm = str(em.get("From",""))
        to = str(em.get("To",""))
        subject = str(em.get("Subject",""))
        dt_tuple = email.utils.parsedate_to_datetime(em.get("Date")) if em.get("Date") else dt.datetime.utcnow()
        body_text, body_html = "", None
        attachments = []
        # parse parts
        if em.is_multipart():
            for part in em.walk():
                ctype = part.get_content_type()
                disp = part.get("Content-Disposition","") or ""
                if ctype == "text/plain" and "attachment" not in disp.lower():
                    try:
                        body_text += part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
                    except Exception:
                        pass
                elif ctype == "text/html" and "attachment" not in disp.lower():
                    try:
                        body_html = (part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore"))
                    except Exception:
                        pass
                elif "attachment" in disp.lower():
                    filename = part.get_filename() or "attachment.bin"
                    content = part.get_payload(decode=True) or b""
                    path = save_attachment_bytes(str(uid), filename, content)
                    attachments.append({"filename": filename, "path": path, "content_type": ctype})
        else:
            payload = em.get_payload(decode=True) or b""
            ctype = em.get_content_type()
            if ctype == "text/html":
                body_html = payload.decode("utf-8", errors="ignore")
            else:
                body_text = payload.decode("utf-8", errors="ignore")

        # importance/class
        aclass, importance = simple_alert_parse(subject, body_text or body_html or "")

        # save to DB (skip duplicates by message-id+uid)
        message_id = em.get("Message-Id") or em.get("Message-ID")
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM messages WHERE imap_uid=? OR message_id=?", (uid, message_id))
        exists = cur.fetchone()
        if not exists:
            cur.execute("""
                INSERT INTO messages(imap_uid, message_id, sender, recipients, subject, text, html, attachments_json, created_at, alert_class, importance)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (uid, message_id, frm, to, subject, body_text, body_html, json.dumps(attachments), dt_tuple.isoformat(), aclass, importance))
            conn.commit()
            new_id = cur.lastrowid
            new_count += 1
            # notify
            notify_caia({
                "source": "mail-bridge",
                "event": "new_email",
                "id": new_id,
                "from": frm, "to": to, "subject": subject,
                "date": dt_tuple.isoformat(),
                "has_attachments": bool(attachments)
            })
            if aclass and importance >= ALERT_IMPORTANCE_MIN:
                tg_notify(f"[{aclass}] {subject}")
        conn.close()
        last_uid = max(last_uid, uid)
    set_meta("last_uid", str(last_uid))
    M.logout()
    return new_count

async def poll_loop():
    while True:
        try:
            await imap_poll_once()
        except Exception:
            pass
        await asyncio.sleep(max(30, POLL_INTERVAL_SEC))

# ---------- Routes ----------
@app.get("/health")
def health():
    ok_imap = bool(IMAP_HOST and IMAP_USER and IMAP_PASS)
    ok_smtp = bool(SMTP_HOST or SENDGRID_API_KEY)
    return {"ok": True, "imap_configured": ok_imap, "smtp_configured": ok_smtp, "auto_run": AUTO_RUN}

@app.on_event("startup")
async def on_startup():
    os.makedirs(ATTACH_DIR, exist_ok=True)
    if AUTO_RUN and (IMAP_HOST and IMAP_USER and IMAP_PASS):
        asyncio.create_task(poll_loop())

# Inbound webhook (optional, multipart form). Token must match INBOUND_TOKEN
@app.post("/inbound")
async def inbound(
    token: str = Query(..., description="INBOUND_TOKEN"),
    from_: str = Form(alias="from"),
    to: str = Form(...),
    subject: str = Form(""),
    text: str = Form(""),
    html: Optional[str] = Form(None),
    attachments: Optional[List[UploadFile]] = File(None),
):
    if not INBOUND_TOKEN or token != INBOUND_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")

    # Save attachments to disk
    att_list = []
    disk_key = f"wb-{int(dt.datetime.utcnow().timestamp())}"
    for f in (attachments or []):
        raw = await f.read()
        path = save_attachment_bytes(disk_key, f.filename or "attach.bin", raw)
        att_list.append({"filename": f.filename, "path": path, "content_type": f.content_type or "application/octet-stream"})

    aclass, importance = simple_alert_parse(subject, text or html or "")
    now = dt.datetime.utcnow().isoformat()
    conn = db()
    conn.execute("""
        INSERT INTO messages(sender, recipients, subject, text, html, attachments_json, created_at, alert_class, importance)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (from_, to, subject, text, html, json.dumps(att_list), now, aclass, importance))
    msg_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit(); conn.close()

    notify_caia({"source": "mail-bridge", "event": "new_email", "id": msg_id, "from": from_, "to": to, "subject": subject, "date": now, "has_attachments": bool(att_list)})
    if aclass and importance >= ALERT_IMPORTANCE_MIN:
        tg_notify(f"[{aclass}] {subject}")

    return {"ok": True, "id": msg_id}

# List inbox
@app.get("/inbox.json")
def inbox_json(limit: int = 20, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    limit = max(1, min(200, limit))
    conn = db()
    rows = conn.execute("""
        SELECT id, sender, recipients, subject, substr(text,1,500) AS text, created_at, alert_class, importance,
               CASE WHEN (attachments_json IS NOT NULL AND length(attachments_json) > 2 AND attachments_json != '[]') THEN 1 ELSE 0 END AS has_attachments,
               deleted
        FROM messages ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return {"ok": True, "messages": [dict(r) for r in rows]}

# View single
@app.get("/mail/view")
def mail_view(id: int, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    row = conn.execute("SELECT * FROM messages WHERE id=?", (id,)).fetchone()
    conn.close()
    if not row: raise HTTPException(status_code=404, detail="not found")
    data = dict(row)
    data["attachments"] = json.loads(data.pop("attachments_json") or "[]")
    return {"ok": True, "message": data}

# Download attachment by index (serve from disk)
@app.get("/mail/attach")
def mail_attach(id: int, idx: int = 0, download: int = 1, token: Optional[str] = Query(None), request: Request = None):
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
    path = f.get("path")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="file missing")
    filename = f.get("filename", f"attach-{id}-{idx}")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return StreamingResponse(open(path, "rb"), media_type=f.get("content_type","application/octet-stream"), headers=headers)

# Send email (JSON, base64 attachments)
@app.post("/mail/send")
def mail_send(payload: SendMailPayload, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    send_email(payload)
    return {"ok": True}

# Send via multipart (file upload)
@app.post("/mail/send-multipart")
async def mail_send_multipart(
    to: List[EmailStr] = Form(...),
    subject: str = Form(...),
    text: str = Form(""),
    html: Optional[str] = Form(None),
    cc: Optional[List[EmailStr]] = Form(None),
    bcc: Optional[List[EmailStr]] = Form(None),
    from_: Optional[EmailStr] = Form(None),
    reply_to: Optional[EmailStr] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
    token: Optional[str] = Query(None),
    request: Request = None
):
    require_token(token, request)
    atts = []
    for f in (files or []):
        raw = await f.read()
        atts.append(AttachmentIn(filename=f.filename, content_b64=base64.b64encode(raw).decode(), content_type=f.content_type))
    payload = SendMailPayload(to=to, subject=subject, text=text, html=html, cc=cc, bcc=bcc, from_=from_, reply_to=reply_to, attachments_b64=atts)
    send_email(payload)
    return {"ok": True}

# Delete (soft delete + IMAP if UID exists)
class DeleteReq(BaseModel):
    id: int

@app.post("/mail/delete")
def mail_delete(req: DeleteReq, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    row = conn.execute("SELECT imap_uid FROM messages WHERE id=?", (req.id,)).fetchone()
    conn.execute("UPDATE messages SET deleted=1 WHERE id=?", (req.id,))
    conn.commit(); conn.close()
    # try IMAP delete
    if row and row["imap_uid"]:
        try:
            import imaplib
            M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT); M.login(IMAP_USER, IMAP_PASS); M.select(IMAP_FOLDER)
            M.uid("store", str(row["imap_uid"]), "+FLAGS", "(\\Deleted)")
            M.expunge(); M.logout()
        except Exception:
            pass
    return {"ok": True, "deleted_id": req.id}

# Manual poll trigger
@app.post("/mail/poll-now")
async def poll_now(token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    n = await imap_poll_once()
    return {"ok": True, "new": n}
