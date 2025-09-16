# app.py (FULL REPLACEMENT — fixed brackets)
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

import ssl
import smtplib
import imaplib
import email
from email.message import EmailMessage

# Optional SDKs
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
    version="2.3.1",
    openapi_version="3.1.0",
    servers=[{"url": "https://mail-bridge.up.railway.app"}],
)

# ENV
def env_get(names, default=""):
    for n in names:
        v = os.getenv(n)
        if v not in (None, ""):
            return v
    return default

SENDER_DEFAULT = os.getenv("SENDER_DEFAULT", "no-reply@example.com")
INBOUND_TOKEN  = os.getenv("INBOUND_TOKEN", "")
AUTH_TOKEN     = os.getenv("AUTH_TOKEN", "")
DB_PATH        = os.getenv("DB_PATH", "mailbridge.sqlite3")

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

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
sg = SendGridAPIClient(api_key=SENDGRID_API_KEY) if (SENDGRID_API_KEY and SendGridAPIClient) else None

# DB
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

# Helpers
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

def b64_of_upload(f: UploadFile) -> str:
    data = f.file.read()
    f.file.seek(0)
    return base64.b64encode(data).decode("utf-8")

# Models
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

# SMTP Send
def send_via_smtp(payload: SendMailPayload):
    msg = EmailMessage()
    msg["From"] = payload.from_ or SENDER_DEFAULT
    msg["To"] = ", ".join(payload.to)
    msg["Subject"] = payload.subject
    if payload.cc:
        msg["Cc"] = ", ".join(payload.cc)
    if payload.reply_to:
        msg["Reply-To"] = payload.reply_to
    msg.set_content(payload.text or "")
    if payload.html:
        msg.add_alternative(payload.html, subtype="html")
    if payload.attachments_b64:
        for a in payload.attachments_b64:
            raw = base64.b64decode(a.content_b64)
            ctype = a.content_type or "application/octet-stream"
            maintype, subtype = (ctype.split("/", 1) if "/" in ctype else ("application","octet-stream"))
            msg.add_attachment(raw, maintype=maintype, subtype=subtype, filename=a.filename)
    try:
        print("[SMTP] connecting to", SMTP_HOST, SMTP_PORT, "SSL=", SMTP_SSL)
        if SMTP_SSL:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as s:
                print("[SMTP] login:", SMTP_USER)
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
                print("[SMTP] send OK")
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
                print("[SMTP] send OK (TLS)")
    except Exception as e:
        print("[SMTP ERROR]", e)
        raise

# SendGrid Send
def send_via_sendgrid(payload: SendMailPayload):
    print("[SendGrid] using API key")
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
            att_objs.append(att)
        message.attachment = att_objs
    resp = sg.send(message)
    print("[SendGrid] status:", getattr(resp, "status_code", None))
    return getattr(resp, "status_code", None)

def send_email(payload: SendMailPayload):
    if sg:
        try:
            sc = send_via_sendgrid(payload)
            return {"ok": True, "via": "sendgrid", "status_code": sc}
        except Exception as e:
            print("[SendGrid ERROR]", e, "-> fallback SMTP")
    send_via_smtp(payload)
    return {"ok": True, "via": "smtp"}

# Routes
@app.get("/health")
def health():
    return {"ok": True, "sender": SENDER_DEFAULT, "smtp_user": SMTP_USER, "imap_user": IMAP_USER, "sendgrid": bool(sg), "version": APP_VER}

@app.post("/mail/send")
def mail_send(payload: SendMailPayload, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    res = send_email(payload)
    return {"ok": True, **res}

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

@app.get("/inbox.json")
def inbox_json(limit: int = 10, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    rows = conn.execute("SELECT id, sender, recipients, subject, substr(text,1,500) AS text, created_at FROM messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
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
            }
            for r in rows
        ]
    }
