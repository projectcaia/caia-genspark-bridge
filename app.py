# app.py
import os, base64, re, time, json
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from mailer_sg import send_email_sg
from store import init_db, save_messages, list_messages_since

# === Caia / Assistants & Telegram ===
import requests
from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID   = os.getenv("ASSISTANT_ID")
THREAD_ID      = os.getenv("THREAD_ID")
TG_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID")

AUTO_RUN = os.getenv("AUTO_RUN", "true").lower() == "true"
ALERT_CLASSES = set([c.strip().upper() for c in os.getenv("ALERT_CLASSES", "SENTINEL,REFLEX,ZENSPARK").split(",") if c.strip()])
ALERT_IMPORTANCE_MIN = float(os.getenv("ALERT_IMPORTANCE_MIN", "0.6"))

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def send_telegram(text: str):
    if not (TG_TOKEN and TG_CHAT_ID): return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT_ID, "text": text})
    except Exception:
        pass

def safe_trunc(s: str, n: int = 3000) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + "\n...[truncated]"

REFLEX_KEYS = [r"\bΔ?K200\b", r"\bCOVIX\b", r"\bKOSPI200_F\b", r"\bVIX\b"]

def classify_email(frm: str, subject: str, text: str) -> str:
    s = (subject or "").lower()
    f = (frm or "").lower()
    t = (text or "")
    if "sentinel" in s or "[sentinel]" in s: return "SENTINEL"
    if "[reflex]" in s or any(re.search(k, t, re.IGNORECASE) for k in REFLEX_KEYS): return "REFLEX"
    if "zenspark" in f or "zenspark" in s: return "ZENSPARK"
    return "OTHER"

def importance_score(tag: str, subject: str, text: str) -> float:
    score = 0.0
    tag = (tag or "OTHER").upper()
    s = (subject or "").lower()
    t = (text or "").lower()
    if tag == "SENTINEL": score += 0.8
    if tag == "REFLEX":   score += 0.7
    if tag == "ZENSPARK": score += 0.5
    for kw in ["급락","급등","panic","spike","alert","경보","임계"]:
        if kw in s or kw in t: score += 0.1
    return min(score, 1.0)

def push_to_thread_and_maybe_run(tag: str, frm: str, to_rcpt: str, subject: str, text: str):
    if not (client and ASSISTANT_ID and THREAD_ID):
        return False, "OpenAI client/IDs missing"
    content = (f"[{tag}] inbound mail\nFrom: {frm}\nTo: {to_rcpt}\nSubject: {subject}\n\n{safe_trunc(text)}")
    client.beta.threads.messages.create(thread_id=THREAD_ID, role="user", content=content)
    if AUTO_RUN:
        client.beta.threads.runs.create(thread_id=THREAD_ID, assistant_id=ASSISTANT_ID,
                                        instructions="센티넬/리플렉스/젠스파크 메일이면 요약 및 전략 초안 생성")
    return True, "ok"
# === 끝 ===

APP = FastAPI(title="Caia Mail Bridge – SendGrid")

# ── ENV
SENDER_DEFAULT = os.getenv("SENDER_DEFAULT")
INBOUND_TOKEN  = os.getenv("INBOUND_TOKEN")
AUTH_TOKEN     = os.getenv("AUTH_TOKEN")  # /status, /inbox 보호용

def _guard(token: Optional[str]) -> bool:
    return (AUTH_TOKEN is None) or (token == AUTH_TOKEN)

# ── Schemas
class SendReq(BaseModel):
    to: List[str]
    subject: str
    text: str = Field("", description="plain text")
    html: Optional[str] = None
    cc: Optional[List[str]] = None
    bcc: Optional[List[str]] = None
    attachments_b64: Optional[List[dict]] = None

class NLReq(BaseModel):
    command: str
    default_to: Optional[List[str]] = None

# ── Startup
@APP.on_event("startup")
def on_startup():
    init_db()
    # 로드된 라우트 로그(디버그용)
    try:
        import logging
        routes = ", ".join(sorted([getattr(r, "path", str(r)) for r in APP.router.routes]))
        logging.getLogger("uvicorn.error").info(f"[Caia] Routes: {routes}")
    except Exception:
        pass

# ── Ping/Health/Status
@APP.get("/ping")
def ping(): return {"pong": True}

@APP.get("/health")
def health(): return {"ok": True, "sender": SENDER_DEFAULT}

@APP.get("/status", response_class=HTMLResponse)
def status(token: Optional[str] = None):
    if not _guard(token): return HTMLResponse("<h3>401 Unauthorized</h3>", status_code=401)
    return HTMLResponse(f"""
    <html><body>
      <h2>Caia Mail Bridge: OK</h2>
      <ul>
        <li>Sender: {SENDER_DEFAULT}</li>
        <li>Inbound token set: {"YES" if INBOUND_TOKEN else "NO"}</li>
      </ul>
      <p><a href="/inbox?limit=10&token={token or ''}">최근 수신 보기</a></p>
    </body></html>""")

# ── 발신(JSON)
@APP.post("/mail/send")
async def api_send(req: SendReq):
    subject = (req.subject or "").strip() or "(제목 없음)"
    text    = (req.text or "").strip() or "(내용 없음)"
    await send_email_sg(mail_from=SENDER_DEFAULT, to=req.to, subject=subject,
                        text=text, html=req.html, cc=req.cc, bcc=req.bcc,
                        attachments_b64=req.attachments_b64)
    return {"ok": True}

# ── 발신(Form)
@APP.post("/mail/send-form")
async def api_send_form(
    to: str = Form(...), subject: str = Form(""), text: str = Form(""),
    html: str = Form(None), files: List[UploadFile] = File(default_factory=list),
):
    subject = (subject or "").strip() or "(제목 없음)"
    text    = (text or "").strip() or "(내용 없음)"
    atts = []
    for f in files:
        b = await f.read()
        atts.append({"filename": f.filename, "content_b64": base64.b64encode(b).decode()})
    await send_email_sg(mail_from=SENDER_DEFAULT,
                        to=[x.strip() for x in to.split(",") if x.strip()],
                        subject=subject, text=text, html=html, attachments_b64=atts)
    return {"ok": True}

# ── 자연어 → 발신(JSON)
@APP.post("/mail/nl")
async def api_nl(req: NLReq):
    to = req.default_to or []
    cmd = (req.command or "").strip()
    subj = None; body = None
    emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', cmd)
    if emails: to = list(set(to + emails))
    m_subj = re.search(r'(?:제목|subject)\\s*(?:은|:)?\\s*([^\\n,]+)', cmd, flags=re.IGNORECASE)
    if m_subj: subj = m_subj.group(1).strip().strip('"').strip("'")
    m_body = re.search(r'(?:내용|message|body)\\s*(?:은|:)?\\s*(.+)$', cmd, flags=re.IGNORECASE)
    if m_body: body = m_body.group(1).strip()
    if not body and subj and subj in cmd:
        tail = cmd.split(subj, 1)[-1]; m2 = re.search(r'(?:내용|보내)\\s*(?:은|를|:)?\\s*(.+)$', tail)
        if m2: body = m2.group(1).strip()
    if not to: return {"ok": False, "error": "받는사람 이메일이 필요해."}
    subj = (subj or "").strip() or "(제목 없음)"; body = (body or "").strip() or "(내용 없음)"
    await send_email_sg(mail_from=SENDER_DEFAULT, to=to, subject=subj, text=body)
    return {"ok": True, "to": to, "subject": subj}

# ── 수신(Webhook: SendGrid Inbound Parse)
@APP.post("/inbound/sen")
async def inbound_parse(request: Request, token: str):
    if INBOUND_TOKEN and token != INBOUND_TOKEN:
        raise HTTPException(401, "invalid token")
    form = await request.form()
    frm     = form.get("from", ""); to_rcpt = form.get("to", "")
    subject = (form.get("subject", "") or "").strip() or "(제목 없음)"
    text    = (form.get("text", "") or "").strip() or "(내용 없음)"
    html    = form.get("html", None)

    attachments = []
    try: n = int(form.get("attachments", 0))
    except: n = 0
    for i in range(1, n + 1):
        f = form.get(f"attachment{i}")
        if hasattr(f, "filename"):
            b = await f.read()
            attachments.append({"filename": f.filename, "content_b64": base64.b64encode(b).decode()})

    save_messages([{
        "from": frm, "to": to_rcpt, "subject": subject,
        "date": time.strftime("%a, %d %b %Y %H:%M:%S %z"),
        "text": text, "html": html, "attachments": attachments
    }])

    try:
        tag = classify_email(frm, subject, text)
        imp = importance_score(tag, subject, text)
        ok, info = push_to_thread_and_maybe_run(tag, frm, to_rcpt, subject, text)
        if (tag in ALERT_CLASSES) or (imp >= ALERT_IMPORTANCE_MIN):
            send_telegram(f"📬 {tag} 메일 감지\n제목: {subject}\n보낸사람: {frm}\n중요도: {imp:.2f}\n"
                          f"→ 스레드 기록{' 및 판단 실행' if AUTO_RUN else ''}")
    except Exception as e:
        send_telegram(f"⚠️ Caia 게이트웨이 처리 실패: {e}")

    return {"ok": True}

# ── 최근 수신(HTML/JSON)
@APP.get("/inbox", response_class=HTMLResponse)
def inbox(limit: int = 10, token: Optional[str] = None):
    if not _guard(token): return HTMLResponse("<h3>401 Unauthorized</h3>", status_code=401)
    rows = list_messages_since(None, limit)
    rows_html = "".join([
        f"<li><b>{r.get('subject','')}</b><br/>From: {r.get('from','')}<br/>To: {r.get('to','')}<br/>"
        f"<pre style='white-space:pre-wrap'>{(r.get('text','') or '')[:1000]}</pre><hr/></li>"
        for r in rows
    ]) or "<li>(수신 없음)</li>"
    return HTMLResponse(f"<html><body><h2>최근 수신 {limit}개</h2><ul>{rows_html}</ul></body></html>")

@APP.get("/inbox.json")
def inbox_json(limit: int = 10, token: Optional[str] = None):
    if not _guard(token): return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    rows = list_messages_since(None, limit)
    return {"ok": True, "messages": rows}
