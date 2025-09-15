# app.py (FULL REPLACEMENT)
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

from server.utils.telegram_notify import send_approval_request


# --- i18n env helper (supports Korean aliases) ---
def env_get(names, default=""):
    for n in names:
        v = os.getenv(n)
        if v not in (None, ""):
            return v
    return default
# --- Optional SDKs (환경변수 없으면 비활성) ---
try:
    from openai import OpenAI  # pip openai>=1.40
except Exception:
    OpenAI = None

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Email, To, Cc, Bcc, Content, Attachment
except Exception:
    SendGridAPIClient = None

APP_VER = "2025-08-17"

# === FastAPI (servers 명시: /openapi.json에 포함됨) ===
app = FastAPI(
    title="Caia Mail Bridge – SendGrid",
    version="1.2.0",
    openapi_version="3.1.0",
    servers=[{"url": "https://worker-production-4369.up.railway.app"}],
)

# === ENV ===
SENDER_DEFAULT = os.getenv("SENDER_DEFAULT", "no-reply@example.com")
INBOUND_TOKEN  = os.getenv("INBOUND_TOKEN", "")
AUTH_TOKEN     = os.getenv("AUTH_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ASSISTANT_ID   = os.getenv("ASSISTANT_ID", "")
THREAD_ID      = os.getenv("THREAD_ID", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
AUTO_RUN      = os.getenv("AUTO_RUN", "false").lower() == "true"
ALERT_CLASSES = set([s.strip().upper() for s in os.getenv("ALERT_CLASSES","SENTINEL,REFLEX,ZENSPARK").split(",") if s.strip()])
AUTO_NOTIFY_INBOUND = env_get(["AUTO_NOTIFY_INBOUND"], "true").lower() in ("1","true","yes")
ALERT_IMPORTANCE_MIN = float(os.getenv("ALERT_IMPORTANCE_MIN", "0.6"))
DB_PATH       = os.getenv("DB_PATH", "mailbridge.sqlite3")

# --- Approval rules ---
APPROVAL_SENDERS = set(s.strip().lower() for s in os.getenv("APPROVAL_SENDERS", "").split(",") if s.strip())
APPROVAL_IMPORTANCE_MIN = float(os.getenv("APPROVAL_IMPORTANCE_MIN", "0.8"))

client = OpenAI(api_key=OPENAI_API_KEY) if (OPENAI_API_KEY and OpenAI) else None
sg = SendGridAPIClient(api_key=SENDGRID_API_KEY) if (SENDGRID_API_KEY and SendGridAPIClient) else None

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
        importance REAL,
        needs_approval INTEGER DEFAULT 0,
        approved INTEGER DEFAULT 0,
        processed INTEGER DEFAULT 0,
        deleted INTEGER DEFAULT 0,
        replied INTEGER DEFAULT 0,
        priority TEXT DEFAULT 'normal',
        auto_reply INTEGER DEFAULT 0
    )""")
    # ensure new columns for existing DBs
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN needs_approval INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN approved INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN processed INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN deleted INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN replied INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN priority TEXT DEFAULT 'normal'")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN auto_reply INTEGER DEFAULT 0")
    except Exception:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ersp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER,
            event TEXT,
            interpretation TEXT,
            lesson TEXT,
            if_then TEXT,
            FOREIGN KEY(message_id) REFERENCES messages(id)
        )
        """
    )
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
    """Validate AUTH_TOKEN or INBOUND_TOKEN on protected endpoints."""
    incoming = token_qs
    if request:
        # Allow token via Authorization Bearer or custom header
        incoming = incoming or _bearer_from_header(request) or request.headers.get("X-Auth-Token")
    if incoming not in {AUTH_TOKEN, INBOUND_TOKEN}:
        raise HTTPException(status_code=401, detail="Unauthorized")

def require_inbound_token(token_qs: Optional[str] = None, request: Optional[Request] = None):
    """Strictly validate INBOUND_TOKEN for webhook/intake calls."""
    incoming = token_qs
    if request:
        incoming = incoming or request.headers.get("X-Auth-Token") or _bearer_from_header(request)
    if not INBOUND_TOKEN or incoming != INBOUND_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")

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

def needs_approval_check(sender: str, importance: float, attachments: list) -> bool:
    """Determine if mail needs manual approval."""
    if attachments:
        return True
    if importance >= APPROVAL_IMPORTANCE_MIN:
        return True
    if sender.lower() in APPROVAL_SENDERS:
        return True
    return False

def telegram_notify(text: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception:
        pass

def assistants_log_and_maybe_run(sender: str, recipients: str, subject: str, text: str, html: Optional[str]):
    if not (client and THREAD_ID):
        return {"thread_message_id": None, "run_id": None}
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


def generate_ersp(subject: str, text: str):
    """Generate ERSP (event, interpretation, lesson, if_then) using OpenAI."""
    if not client:
        return None
    prompt = (
        "Analyze the following email and extract an ERSP summary as JSON with keys "
        "'event', 'interpretation', 'lesson', and 'if_then'.\n"
        f"Subject: {subject or ''}\n"
        f"Body: {text or ''}\n"
        "Respond with a JSON object."
    )
    try:
        resp = client.responses.create(
            model="gpt-4o-mini",
            input=prompt,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = resp.output[0].content[0].text
        return json.loads(content)
    except Exception:
        return None

def b64_of_upload(f: UploadFile) -> str:
    data = f.file.read()
    f.file.seek(0)
    return base64.b64encode(data).decode("utf-8")

# === Models ===
class SendMailPayload(BaseModel):
    to: List[EmailStr]
    subject: str
    text: Optional[str] = ""
    html: Optional[str] = None
    cc: Optional[List[EmailStr]] = None
    bcc: Optional[List[EmailStr]] = None
    attachments_b64: Optional[List[dict]] = None  # [{filename, content_b64, content_type?}]

class ToolSendReq(BaseModel):
    """툴에서 쓰기 쉬운 최소 필드 모델"""
    to: List[EmailStr]
    subject: str
    text: str
    html: Optional[str] = None

class WebhookAttachment(BaseModel):
    filename: str
    content_b64: str
    content_type: str | None = None

class WebhookMailPayload(BaseModel):
    sender: str
    recipients: str
    subject: str | None = ""
    text: str | None = ""
    attachments: List[WebhookAttachment] | None = None

# === Routes ===
@app.get("/ping")
def ping():
    return {"pong": True}

@app.get("/health")
def health():
    return {"ok": True, "sender": SENDER_DEFAULT, "version": APP_VER}

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
    # inbound token check
    require_inbound_token(token, request)

    # serialize attachments
    atts = []
    if attachments:
        for f in attachments:
            atts.append({
                "filename": f.filename,
                "content_b64": b64_of_upload(f),
                "content_type": f.content_type or "application/octet-stream"
            })

    # importance & class
    aclass, importance = simple_alert_parse(subject, text)
    needs_appr = needs_approval_check(from_, importance, atts)

    # save to DB
    now = dt.datetime.utcnow().isoformat()
    conn = db()
    conn.execute("""
        INSERT INTO messages(sender, recipients, subject, text, html, attachments_json, created_at, alert_class, importance, needs_approval, approved, processed)
        VALUES(?,?,?,?,?,?,?,?,?,?,0,0)
    """, (from_, to, subject, text, html, json.dumps(atts), now, aclass, importance, needs_appr))
    msg_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    ersp = generate_ersp(subject, text)
    if ersp:
        conn.execute(
            "INSERT INTO ersp(message_id, event, interpretation, lesson, if_then) VALUES(?,?,?,?,?)",
            (
                msg_id,
                ersp.get("event"),
                ersp.get("interpretation"),
                ersp.get("lesson"),
                ersp.get("if_then"),
            ),
        )
    conn.commit()
    conn.close()

    # Assistants v2 log (+ optional run)
    res = assistants_log_and_maybe_run(from_, to, subject, text, html)

    if needs_appr:
        try:
            send_approval_request(msg_id, from_, subject)
        except Exception:
            pass
    elif importance >= ALERT_IMPORTANCE_MIN:
        telegram_notify(f"[{aclass or 'INFO'}] {subject}\nfrom {from_}\n#{msg_id}")

    return {"ok": True, "id": msg_id, "assistant": res, "alert_class": aclass, "importance": importance}


@app.post("/webhook/mail")
def webhook_mail(payload: WebhookMailPayload, request: Request):
    require_inbound_token(None, request)

    aclass, importance = simple_alert_parse(payload.subject, payload.text)
    atts = [a.model_dump() for a in (payload.attachments or [])]
    needs_appr = needs_approval_check(payload.sender, importance, atts)
    now = dt.datetime.utcnow().isoformat()
    conn = db()
    conn.execute(
        """
        INSERT INTO messages(sender, recipients, subject, text, html, attachments_json, created_at, alert_class, importance, needs_approval, approved, processed)
        VALUES(?,?,?,?,?,?,?,?,?,?,0,0)
        """,
        (
            payload.sender,
            payload.recipients,
            payload.subject,
            payload.text,
            None,
            json.dumps(atts),
            now,
            aclass,
            importance,
            needs_appr,
        ),
    )
    msg_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    ersp = generate_ersp(payload.subject, payload.text)
    if ersp:
        conn.execute(
            "INSERT INTO ersp(message_id, event, interpretation, lesson, if_then) VALUES(?,?,?,?,?)",
            (
                msg_id,
                ersp.get("event"),
                ersp.get("interpretation"),
                ersp.get("lesson"),
                ersp.get("if_then"),
            ),
        )
    conn.commit()
    conn.close()
    if needs_appr:
        try:
            send_approval_request(msg_id, payload.sender, payload.subject or "")
        except Exception:
            pass
    elif importance >= ALERT_IMPORTANCE_MIN:
        telegram_notify(f"[{aclass or 'INFO'}] {payload.subject}\nfrom {payload.sender}\n#{msg_id}")
    return {"ok": True, "id": msg_id}


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
    }}


@app.get("/mail/ersp")
def mail_ersp(id: int = Query(...), token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    row = conn.execute(
        "SELECT event, interpretation, lesson, if_then FROM ersp WHERE message_id=?",
        (id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "ersp": dict(row)}

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

@app.post("/mail/approve")
def mail_approve(id: int = Query(...), token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    row = conn.execute("SELECT * FROM messages WHERE id=?", (id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="not found")
    conn.execute("UPDATE messages SET approved=1, processed=1, needs_approval=0 WHERE id=?", (id,))
    conn.commit()
    attachments = json.loads(row["attachments_json"] or "[]")
    saved = []
    for a in attachments:
        try:
            data = base64.b64decode(a.get("content_b64", ""))
            fname = f"mail_{id}_{a.get('filename','attachment')}"
            with open(fname, "wb") as f:
                f.write(data)
            saved.append(fname)
        except Exception:
            pass
    conn.close()
    if sg and row["sender"]:
        try:
            msg = Mail(from_email=Email(SENDER_DEFAULT), to_emails=[To(row["sender"])], subject=f"Re: {row['subject']}", plain_text_content=Content("text/plain", "Your mail has been processed."))
            sg.send(msg)
        except Exception:
            pass
    return {"ok": True, "id": id, "saved": saved}

@app.post("/mail/reject")
def mail_reject(id: int = Query(...), token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    conn = db()
    row = conn.execute("SELECT id FROM messages WHERE id=?", (id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="not found")
    conn.execute("DELETE FROM messages WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return {"ok": True, "id": id}

@app.post("/mail/send")
def mail_send(payload: SendMailPayload, token: Optional[str] = Query(None), request: Request = None):
    require_token(token, request)
    if not sg:
        raise HTTPException(status_code=501, detail="SENDGRID_API_KEY not configured")

    message = Mail(
        from_email=Email(SENDER_DEFAULT),
        to_emails=[To(addr) for addr in payload.to],
        subject=payload.subject,
        plain_text_content=Content("text/plain", payload.text or ""),
        html_content=Content("text/html", payload.html or (payload.text or "")),
    )
    if payload.cc:
        message.cc = [Cc(addr) for addr in payload.cc]
    if payload.bcc:
        message.bcc = [Bcc(addr) for addr in payload.bcc]
    if payload.attachments_b64:
        att_objs = []
        for a in payload.attachments_b64:
            att = Attachment()
            att.file_content = a["content_b64"]
            att.file_name = a["filename"]
            att_objs.append(att)
        message.attachment = att_objs

    resp = sg.send(message)
    return {
        "ok": True,
        "message": "메일이 성공적으로 발송되었습니다.",
        "status_code": getattr(resp, "status_code", None)
    }

# === 툴 친화 간단 발신 (스키마 properties 포함용) ===
@app.post("/tool/send")
def tool_send(payload: ToolSendReq, token: Optional[str] = Query(None), request: Request = None):
    """
    payload 예시:
    {
      "to": ["a@b.com"],
      "subject": "s",
      "text": "t",
      "html": null
    }
    """
    require_token(token, request)
    model = SendMailPayload(
        to=payload.to,
        subject=payload.subject,
        text=payload.text,
        html=payload.html,
    )
    # mail_send의 표준 응답 그대로 전달
    res = mail_send(model, token, request)
    # 혹시라도 상위 UI에서 별도 메시지를 기대하면 아래처럼 보강 가능:
    # res.update({"tool_hint": "tool/send executed"})
    return res


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

@app.post("/webhook/telegram")
def webhook_telegram(request: Request):
    # Telegram bot webhook receiver.
    # If the user replies "승인" or "거부" to a bot message like "Mail #<id> ...",
    # we flip approval flags in the DB accordingly.
    # Auth: header X-Auth-Token must equal INBOUND_TOKEN (same as mail webhook).
    require_inbound_token(None, request)
    try:
        upd = request.json()
    except Exception:
        upd = None
    if upd is None:
        try:
            upd = json.loads(request.body())
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

    msg = upd.get("message") or {}
    text = (msg.get("text") or "").strip()
    reply = msg.get("reply_to_message") or {}
    original_text = (reply.get("text") or "")
    # Extract mail id from "Mail #123" format
    mail_id = None
    try:
        import re as _re
        m = _re.search(r"Mail\s*#(\d+)", original_text)
        if m:
            mail_id = int(m.group(1))
    except Exception:
        pass
    if not mail_id:
        return {"ok": True, "skipped": "no_mail_id"}
    low = text.lower()
    if low.startswith("승인") or low.startswith("approve"):
        conn = db()
        conn.execute("UPDATE messages SET approved=1, processed=1, needs_approval=0 WHERE id=?", (mail_id,))
        conn.commit()
        conn.close()
        telegram_notify(f"✅ 승인 처리 완료 (ID {mail_id})")
        return {"ok": True, "action": "approved", "id": mail_id}
    if low.startswith("거부") or low.startswith("reject"):
        conn = db()
        conn.execute("UPDATE messages SET approved=0, processed=1, needs_approval=0 WHERE id=?", (mail_id,))
        conn.commit()
        conn.close()
        telegram_notify(f"🚫 거부 처리 완료 (ID {mail_id})")
        return {"ok": True, "action": "rejected", "id": mail_id}
    return {"ok": True, "skipped": "no_action"}
