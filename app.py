# app.py
import os, re, time, json, base64, requests
from typing import List, Optional, Dict
from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from mailer_sg import send_email_sg
from store import init_db, save_messages, list_messages_since

# ====== App ======
app = FastAPI(title="Caia Mail Bridge – SendGrid")

# ====== ENV ======
SENDER_DEFAULT = os.getenv("SENDER_DEFAULT")           # 예: caia@caia-agent.com
INBOUND_TOKEN  = os.getenv("INBOUND_TOKEN")            # 예: 랜덤 토큰
AUTH_TOKEN     = os.getenv("AUTH_TOKEN")               # /status, /inbox 보호 토큰

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID   = os.getenv("ASSISTANT_ID")
THREAD_ID      = os.getenv("THREAD_ID")

TG_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID     = os.getenv("TELEGRAM_CHAT_ID")

AUTO_RUN = os.getenv("AUTO_RUN", "true").lower() == "true"
ALERT_CLASSES = set([c.strip().upper() for c in os.getenv("ALERT_CLASSES", "SENTINEL,REFLEX,ZENSPARK").split(",") if c.strip()])
ALERT_IMPORTANCE_MIN = float(os.getenv("ALERT_IMPORTANCE_MIN", "0.6"))

# OpenAI client (assistants v2)
try:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    client = None

# ====== Utils ======
def _guard(token: Optional[str]) -> bool:
    return (AUTH_TOKEN is None) or (token == AUTH_TOKEN)

def send_telegram(text: str):
    if not (TG_TOKEN and TG_CHAT_ID): 
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text}
        )
    except Exception:
        pass

def safe_trunc(s: str, n: int = 3000) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + "\n...[truncated]"

# 간단 분류 규칙
REFLEX_KEYS = [r"\bΔ?K200\b", r"\bCOVIX\b", r"\bKOSPI200_F\b", r"\bVIX\b"]

def classify_email(frm: str, subject: str, text: str) -> str:
    s = (subject or "").lower()
    f = (frm or "").lower()
    t = (text or "")
    if "sentinel" in s or "[sentinel]" in s:
        return "SENTINEL"
    if "[reflex]" in s or any(re.search(k, t, re.IGNORECASE) for k in REFLEX_KEYS):
        return "REFLEX"
    if "zenspark" in f or "zenspark" in s:
        return "ZENSPARK"
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
    """스레드에 user 메시지 기록, AUTO_RUN이면 run 트리거"""
    if not (client and ASSISTANT_ID and THREAD_ID):
        return False, "OpenAI client/IDs missing"
    content = f"[{tag}] inbound mail\nFrom: {frm}\nTo: {to_rcpt}\nSubject: {subject}\n\n{safe_trunc(text)}"
    client.beta.threads.messages.create(thread_id=THREAD_ID, role="user", content=content)
    if AUTO_RUN:
        client.beta.threads.runs.create(
            thread_id=THREAD_ID, assistant_id=ASSISTANT_ID,
            instructions="센티넬/리플렉스/젠스파크 메일이면 요약 및 전략 초안 생성"
        )
    return True, "ok"

# ====== Schemas ======
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

# ====== Startup ======
@app.on_event("startup")
def on_startup():
    init_db()
    try:
        import logging
        routes = ", ".join(sorted([getattr(r, "path", str(r)) for r in app.router.routes]))
        logging.getLogger("uvicorn.error").info(f"[Caia] Routes: {routes}")
    except Exception:
        pass
    send_telegram("🚀 Caia Mail Bridge 서버 시작됨.")

@app.on_event("shutdown")
def on_shutdown():
    # 롤링배포 노이즈 줄이려면 여기 필터링 로직 추가 가능
    send_telegram("🛑 Caia Mail Bridge 서버 종료됨.")

# ====== Health / Status ======
@app.get("/ping")
def ping(): return {"pong": True}

@app.get("/health")
def health(): return {"ok": True, "sender": SENDER_DEFAULT}

@app.get("/status", response_class=HTMLResponse)
def status(token: Optional[str] = None):
    if not _guard(token):
        return HTMLResponse("<h3>401 Unauthorized</h3>", status_code=401)
    return HTMLResponse(f"""
    <html><body>
      <h2>Caia Mail Bridge: OK</h2>
      <ul>
        <li>Sender: {SENDER_DEFAULT}</li>
        <li>Inbound token set: {"YES" if INBOUND_TOKEN else "NO"}</li>
      </ul>
      <p><a href="/inbox?limit=10&token={token or ''}">최근 수신 보기</a></p>
    </body></html>""")

# ====== 발신 ======
@app.post("/mail/send")
async def api_send(req: SendReq):
    subject = (req.subject or "").strip() or "(제목 없음)"
    text    = (req.text or "").strip() or "(내용 없음)"
    await send_email_sg(
        mail_from=SENDER_DEFAULT, to=req.to, subject=subject,
        text=text, html=req.html, cc=req.cc, bcc=req.bcc,
        attachments_b64=req.attachments_b64
    )
    return {"ok": True}

@app.post("/mail/send-form")
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
    await send_email_sg(
        mail_from=SENDER_DEFAULT,
        to=[x.strip() for x in to.split(",") if x.strip()],
        subject=subject, text=text, html=html, attachments_b64=atts
    )
    return {"ok": True}

@app.post("/mail/nl")
async def api_nl(req: NLReq):
    to = req.default_to or []
    cmd = (req.command or "").strip()
    subj = None; body = None

    emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', cmd)
    if emails: to = list(set(to + emails))

    m_subj = re.search(r'(?:제목|subject)\s*(?:은|:)?\s*([^\n,]+)', cmd, flags=re.IGNORECASE)
    if m_subj: subj = m_subj.group(1).strip().strip('"').strip("'")
    m_body = re.search(r'(?:내용|message|body)\s*(?:은|:)?\s*(.+)$', cmd, flags=re.IGNORECASE)
    if m_body: body = m_body.group(1).strip()

    if not body and subj and subj in cmd:
        tail = cmd.split(subj, 1)[-1]
        m2 = re.search(r'(?:내용|보내)\s*(?:은|를|:)?\s*(.+)$', tail)
        if m2: body = m2.group(1).strip()

    if not to:
        return {"ok": False, "error": "받는사람 이메일이 필요해."}

    subj = (subj or "").strip() or "(제목 없음)"
    body = (body or "").strip() or "(내용 없음)"

    await send_email_sg(mail_from=SENDER_DEFAULT, to=to, subject=subj, text=body)
    return {"ok": True, "to": to, "subject": subj}

# ====== 수신 (SendGrid Inbound Parse) ======
@app.post("/inbound/sen")
async def inbound_parse(request: Request, token: str):
    """
    SendGrid Inbound Parse 웹훅:
    - token 인증 → 폼 파싱 → DB 저장 → 분류/중요도 → 스레드 기록(+AUTO_RUN) → (조건)텔레그램
    """
    if INBOUND_TOKEN and token != INBOUND_TOKEN:
        raise HTTPException(401, "invalid token")

    # multipart/form-data or x-www-form-urlencoded
    try:
        form = await request.form()
    except Exception:
        raw = await request.body()
        try:
            from urllib.parse import parse_qs
            form = {k: v[0] if isinstance(v, list) else v for k, v in parse_qs(raw.decode("utf-8", "ignore")).items()}
        except Exception:
            form = {}

    frm     = form.get("from", "")
    to_rcpt = form.get("to", "")
    subject = (form.get("subject", "") or "").strip() or "(제목 없음)"
    text    = (form.get("text", "") or "").strip() or "(내용 없음)"
    html    = form.get("html", None)

    # attachments
    attachments = []
    try:
        n = int(form.get("attachments", 0))
    except Exception:
        n = 0
    for i in range(1, n + 1):
        f = form.get(f"attachment{i}")
        if hasattr(f, "filename"):
            b = await f.read()
            attachments.append({
                "filename": f.filename,
                "content_b64": base64.b64encode(b).decode()
            })

    save_messages([{
        "from": frm,
        "to": to_rcpt,
        "subject": subject,
        "date": time.strftime("%a, %d %b %Y %H:%M:%S %z"),
        "text": text,
        "html": html,
        "attachments": attachments
    }])

    try:
        tag = classify_email(frm, subject, text)
        imp = importance_score(tag, subject, text)
        push_to_thread_and_maybe_run(tag, frm, to_rcpt, subject, text)

        if (tag in ALERT_CLASSES) or (imp >= ALERT_IMPORTANCE_MIN):
            send_telegram(
                f"📬 {tag} 메일 감지\n제목: {subject}\n보낸사람: {frm}\n중요도: {imp:.2f}\n"
                f"→ 스레드 기록{' 및 판단 실행' if AUTO_RUN else ''}"
            )
    except Exception as e:
        send_telegram(f"⚠️ Caia 게이트웨이 처리 실패: {e}")

    return {"ok": True}

# 호환용 alias (콘솔에 /inbound/sendgrid 로 잡아둔 경우)
@app.post("/inbound/sendgrid")
async def inbound_alias(request: Request, token: str):
    return await inbound_parse(request, token)

# ====== Inbox (view) ======
@app.get("/inbox", response_class=HTMLResponse)
def inbox(limit: int = 10, token: Optional[str] = None):
    if not _guard(token):
        return HTMLResponse("<h3>401 Unauthorized</h3>", status_code=401)
    rows = list_messages_since(None, limit)
    rows_html = "".join([
        f"<li><b>{r.get('subject','')}</b><br/>From: {r.get('from','')}<br/>To: {r.get('to','')}<br/>"
        f"<pre style='white-space:pre-wrap'>{(r.get('text','') or '')[:1000]}</pre><hr/></li>"
        for r in rows
    ]) or "<li>(수신 없음)</li>"
    return HTMLResponse(f"<html><body><h2>최근 수신 {limit}개</h2><ul>{rows_html}</ul></body></html>")

@app.get("/inbox.json")
def inbox_json(limit: int = 10, token: Optional[str] = None):
    if not _guard(token):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    rows = list_messages_since(None, limit)
    return {"ok": True, "messages": rows}
