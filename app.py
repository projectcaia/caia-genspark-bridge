# app.py
import os, base64, re, time, json, hashlib
from typing import List, Optional, Dict

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from mailer_sg import send_email_sg
from store import (
    init_db, save_messages, list_messages_since,
    get_message_by_id, kv_get, kv_set
)

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
DEDUPE_TTL_SEC = int(os.getenv("DEDUPE_TTL_SEC", "180"))   # 메일 중복 TTL(초)
RUN_COOLDOWN_SEC = int(os.getenv("RUN_COOLDOWN_SEC", "45"))  # Run 쿨다운(초)

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def send_telegram(text: str):
    if not (TG_TOKEN and TG_CHAT_ID): return
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

# === 간단 규칙(분류) ===
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

# === 중복 방지 ===
def make_fp(frm: str, to_rcpt: str, subject: str, text: str) -> str:
    base = f"{(frm or '').lower()}|{(to_rcpt or '').lower()}|{(subject or '').strip()}|{(text or '')[:200]}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()

def seen_recent(fp: str) -> bool:
    try:
        v = kv_get(f"mailfp:{fp}")
        if not v: return False
        last_ts = int(v)
        return (int(time.time()) - last_ts) < DEDUPE_TTL_SEC
    except Exception:
        return False

def mark_seen(fp: str):
    try:
        kv_set(f"mailfp:{fp}", str(int(time.time())))
    except Exception:
        pass

# === Run 스로틀(쿨다운) ===
def run_cooldown_active() -> bool:
    try:
        v = kv_get("run:last")
        if not v: return False
        return (int(time.time()) - int(v)) < RUN_COOLDOWN_SEC
    except Exception:
        return False

def mark_run_fired():
    try:
        kv_set("run:last", str(int(time.time())))
    except Exception:
        pass

def push_to_thread_and_maybe_run(tag: str, frm: str, to_rcpt: str, subject: str, text: str):
    if not (client and ASSISTANT_ID and THREAD_ID):
        return False, "OpenAI client/IDs missing"
    content = (
        f"[{tag}] inbound mail\n"
        f"From: {frm}\nTo: {to_rcpt}\nSubject: {subject}\n\n"
        f"{safe_trunc(text)}"
    )
    client.beta.threads.messages.create(
        thread_id=THREAD_ID,
        role="user",
        content=content
    )
    # AUTO_RUN은 ALERT_CLASSES 내 태그이고, 쿨다운 중이 아닐 때만
    should_autorun = AUTO_RUN and (tag.upper() in ALERT_CLASSES) and (not run_cooldown_active())
    if should_autorun:
        client.beta.threads.runs.create(
            thread_id=THREAD_ID,
            assistant_id=ASSISTANT_ID,
            instructions="센티넬/리플렉스/젠스파크 메일이면 요약 및 전략 초안 생성"
        )
        mark_run_fired()
    return True, "ok"

# === FastAPI ===
APP = FastAPI(title="Caia Mail Bridge – SendGrid")

# ── ENV
SENDER_DEFAULT = os.getenv("SENDER_DEFAULT")
INBOUND_TOKEN  = os.getenv("INBOUND_TOKEN")
AUTH_TOKEN     = os.getenv("AUTH_TOKEN")  # /status, /inbox, /mail/* 보호용

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
    reply_to: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    categories: Optional[List[str]] = None
    sandbox: bool = False
    track_opens: bool = False
    track_clicks: bool = False

class NLReq(BaseModel):
    command: str
    default_to: Optional[List[str]] = None

# ── Startup
@APP.on_event("startup")
def on_startup():
    init_db()
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
    await send_email_sg(
        mail_from=SENDER_DEFAULT,
        to=req.to, subject=subject, text=text, html=req.html,
        cc=req.cc, bcc=req.bcc, attachments_b64=req.attachments_b64,
        reply_to=req.reply_to, headers=req.headers, categories=req.categories,
        sandbox=req.sandbox, track_opens=req.track_opens, track_clicks=req.track_clicks
    )
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
    await send_email_sg(
        mail_from=SENDER_DEFAULT,
        to=[x.strip() for x in to.split(",") if x.strip()],
        subject=subject, text=text, html=html, attachments_b64=atts
    )
    return {"ok": True}

# ── 자연어 → 발신(JSON)
@APP.post("/mail/nl")
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

# ── 수신(Webhook: SendGrid Inbound Parse)
@APP.post("/inbound/sen")
async def inbound_parse(request: Request, token: str):
    """
    SendGrid Inbound Parse 웹훅 엔드포인트.
    - token 쿼리스트링 인증
    - multipart/form-data / application/x-www-form-urlencoded 모두 처리
    - DB 저장 → (중복 차단) → 분류/중요도 → 스레드 전달(+AUTO_RUN) → (조건)텔레그램 알림
    """
    # 1) 토큰 검증
    if INBOUND_TOKEN and token != INBOUND_TOKEN:
        raise HTTPException(401, "invalid token")

    # 2) 폼 파싱 (multipart / urlencoded 호환)
    try:
        form = await request.form()
    except Exception:
        raw = await request.body()
        try:
            from urllib.parse import parse_qs
            form = {k: v[0] if isinstance(v, list) else v for k, v in parse_qs(raw.decode("utf-8", "ignore")).items()}
        except Exception:
            form = {}

    frm     = form.get("from", "") if isinstance(form, dict) else form.get("from", "")
    to_rcpt = form.get("to", "")   if isinstance(form, dict) else form.get("to", "")
    subject = (form.get("subject", "") or "").strip() or "(제목 없음)"
    text    = (form.get("text", "") or "").strip() or "(내용 없음)"
    html    = form.get("html", None)

    # 3) 첨부 수집
    attachments = []
    try:
        n = int(form.get("attachments", 0))
    except Exception:
        n = 0
    for i in range(1, n + 1):
        f = form.get(f"attachment{i}")
        if hasattr(f, "filename"):  # Starlette UploadFile
            b = await f.read()
            attachments.append({
                "filename": f.filename,
                "content_b64": base64.b64encode(b).decode()
            })

    # 4) 저장
    save_messages([{
        "from": frm, "to": to_rcpt, "subject": subject,
        "date": time.strftime("%a, %d %b %Y %H:%M:%S %z"),
        "text": text, "html": html, "attachments": attachments
    }])

    # 4.5) 중복 차단
    try:
        fp = make_fp(frm, to_rcpt, subject, text)
        if seen_recent(fp):
            send_telegram(f"ℹ️ 중복 감지: '{subject}' (최근 {DEDUPE_TTL_SEC}s)")
            return {"ok": True}
        mark_seen(fp)
    except Exception:
        pass

    # 5) 게이트웨이: 분류 → 스레드 기록 → (옵션)Run → (옵션)텔레그램
    try:
        tag = classify_email(frm, subject, text)
        imp = importance_score(tag, subject, text)

        push_to_thread_and_maybe_run(tag, frm, to_rcpt, subject, text)

        if (tag in ALERT_CLASSES) or (imp >= ALERT_IMPORTANCE_MIN):
            send_telegram(
                f"📬 {tag} 메일 감지\n"
                f"제목: {subject}\n보낸사람: {frm}\n"
                f"중요도: {imp:.2f}\n"
                f"→ 스레드 기록{' 및 판단 실행' if AUTO_RUN else ''}"
            )
    except Exception as e:
        # 인바운드 훅은 200을 유지해야 SendGrid 재시도폭격을 막음
        send_telegram(f"⚠️ Caia 게이트웨이 처리 실패: {e}")

    return {"ok": True}

# ── SendGrid 콘솔에 /inbound/sendgrid 로 등록된 경우 호환용 alias
@APP.post("/inbound/sendgrid")
async def inbound_alias(request: Request, token: str):
    return await inbound_parse(request, token)

# === 단건 조회/요약/포워드 ===

@APP.get("/mail/view")
def mail_view(id: int, token: Optional[str] = None):
    """단건 조회(본문/HTML/첨부). AUTH 보호."""
    if not _guard(token):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    msg = get_message_by_id(id)
    if not msg:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return {"ok": True, "message": msg}

class ForwardReq(BaseModel):
    to: List[str]
    subject: Optional[str] = None
    prepend_text: Optional[str] = None  # 원문 앞에 붙일 안내문 (선택)
    token: Optional[str] = None

@APP.post("/mail/forward")
async def mail_forward(req: ForwardReq, id: int):
    """저장된 첨부까지 포함해 재발송(포워드). token은 body로 받음."""
    if not _guard(req.token):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    msg = get_message_by_id(id)
    if not msg:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    subj = (req.subject or msg["subject"] or "(제목 없음)").strip()
    body = (req.prepend_text or "") + "\n\n" + (msg.get("text") or "")
    atts = msg.get("attachments") or []

    await send_email_sg(
        mail_from=SENDER_DEFAULT,
        to=req.to, subject=subj, text=body,
        html=msg.get("html"), attachments_b64=atts
    )
    return {"ok": True, "to": req.to, "subject": subj, "forwarded_id": id}

class SummReq(BaseModel):
    id: int
    push_to_thread: bool = True
    token: Optional[str] = None

@APP.post("/mail/summarize")
def mail_summarize(req: SummReq):
    """본문 요약(OpenAI). AUTH 보호. (옵션) 스레드 기록."""
    if not _guard(req.token):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    if not client:
        return JSONResponse({"ok": False, "error": "openai_not_configured"}, status_code=500)

    msg = get_message_by_id(req.id)
    if not msg:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    text = msg.get("text") or ""
    subject = msg.get("subject") or ""

    # 스레드에 요약 요청 메시지 기록(Assistants가 처리)
    try:
        client.beta.threads.messages.create(
            thread_id=THREAD_ID,
            role="user",
            content=f"[SUM_REQ] summarize this mail (id={req.id})\n\n"
                    f"다음 메일의 핵심을 5줄 이내로 한국어 요약해줘. "
                    f"핵심 시그널/숫자/액션 아이템을 남겨라.\n\n"
                    f"제목: {subject}\n본문:\n{text}"
        )
        if req.push_to_thread and AUTO_RUN:
            client.beta.threads.runs.create(
                thread_id=THREAD_ID,
                assistant_id=ASSISTANT_ID,
                instructions="위 SUM_REQ를 간결 요약으로 답하라."
            )
        summary = "(요약은 스레드 메시지로 생성됨)"
    except Exception as e:
        summary = f"(요약 요청 실패: {e})"

    return {"ok": True, "id": req.id, "summary": summary}

# ── 최근 수신(HTML/JSON)
@APP.get("/inbox", response_class=HTMLResponse)
def inbox(limit: int = 10, token: Optional[str] = None):
    if not _guard(token):
        return HTMLResponse("<h3>401 Unauthorized</h3>", status_code=401)
    rows = list_messages_since(None, limit)
    rows_html = "".join([
        f"<li><b>{r.get('subject','')}</b>"
        f" <small>{'[첨부]' if r.get('has_attachments') else ''}</small><br/>"
        f"From: {r.get('from','')}<br/>To: {r.get('to','')}<br/>"
        f"<pre style='white-space:pre-wrap'>{(r.get('text','') or '')[:1000]}</pre><hr/></li>"
        for r in rows
    ]) or "<li>(수신 없음)</li>"
    return HTMLResponse(f"<html><body><h2>최근 수신 {limit}개</h2><ul>{rows_html}</ul></body></html>")

@APP.get("/inbox.json")
def inbox_json(limit: int = 10, token: Optional[str] = None):
    if not _guard(token):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    rows = list_messages_since(None, limit)
    return {"ok": True, "messages": rows}
