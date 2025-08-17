import os, re, time, json, base64, requests
from typing import List, Optional, Dict

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException, Response, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from mailer_sg import send_email_sg
from store import init_db, save_messages, list_messages_since, get_message_by_id

# ====== App ======
app = FastAPI(title="Caia Mail Bridge – SendGrid", version="1.2.0")

# ====== ENV ======
SENDER_DEFAULT = os.getenv("SENDER_DEFAULT")           # 예: caia@caia-agent.com
INBOUND_TOKEN  = os.getenv("INBOUND_TOKEN")            # 예: 랜덤 토큰
AUTH_TOKEN     = os.getenv("AUTH_TOKEN")               # /status, /inbox 등 보호 토큰

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID   = os.getenv("ASSISTANT_ID")
THREAD_ID      = os.getenv("THREAD_ID")

AUTO_RUN = os.getenv("AUTO_RUN", "true").lower() == "true"
ALERT_CLASSES = set([c.strip().upper() for c in os.getenv("ALERT_CLASSES", "SENTINEL,REFLEX,ZENSPARK").split(",") if c.strip()])
ALERT_IMPORTANCE_MIN = float(os.getenv("ALERT_IMPORTANCE_MIN", "0.6"))

# (선택) OpenAI Assistants v2
try:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    client = None


# ====== 공용 유틸 ======
def _authorized(token_qs: Optional[str], auth_header: Optional[str]) -> bool:
    """
    두 방식 모두 허용:
      1) 쿼리스트링 ?token=AUTH_TOKEN
      2) Authorization: Bearer AUTH_TOKEN
    """
    if AUTH_TOKEN is None:
        return True
    if token_qs == AUTH_TOKEN:
        return True
    if auth_header and auth_header.lower().startswith("bearer "):
        return (auth_header.split(" ", 1)[1].strip() == AUTH_TOKEN)
    return False

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
    for kw in ["급락", "급등", "panic", "spike", "alert", "경보", "임계"]:
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

class ToolSendReq(BaseModel):
    """툴용: 간단 발신형"""
    to: List[str]
    subject: str
    text: str
    html: Optional[str] = None


# ====== Startup ======
@app.on_event("startup")
def on_startup():
    init_db()


# ====== Health / Status ======
@app.get("/ping")
def ping(): return {"pong": True}

@app.get("/health")
def health(): return {"ok": True, "sender": SENDER_DEFAULT}

@app.get("/status", response_class=HTMLResponse)
def status(token: Optional[str] = None, authorization: Optional[str] = Header(None)):
    if not _authorized(token, authorization):
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
async def api_send(req: SendReq, token: Optional[str] = None, authorization: Optional[str] = Header(None)):
    if not _authorized(token, authorization):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
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
    token: Optional[str] = None, authorization: Optional[str] = Header(None)
):
    if not _authorized(token, authorization):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
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

@app.post("/tool/send")
async def tool_send(req: ToolSendReq, token: Optional[str] = None, authorization: Optional[str] = Header(None)):
    """
    GPT 툴에서 쓰기 좋은 단순 발신 엔드포인트
    """
    if not _authorized(token, authorization):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    subject = (req.subject or "").strip() or "(제목 없음)"
    text    = (req.text or "").strip() or "(내용 없음)"
    await send_email_sg(
        mail_from=SENDER_DEFAULT, to=req.to, subject=subject, text=text, html=req.html
    )
    return {"ok": True}


# ====== 자연어 → 발신(JSON) ======
@app.post("/mail/nl")
async def api_nl(req: NLReq, token: Optional[str] = None, authorization: Optional[str] = Header(None)):
    if not _authorized(token, authorization):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

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
    - token 인증 → 폼 파싱 → DB 저장 → 분류/중요도 → 스레드 기록(+AUTO_RUN)
    """
    if INBOUND_TOKEN and token != INBOUND_TOKEN:
        raise HTTPException(401, "invalid token")

    # multipart/form-data 또는 x-www-form-urlencoded
    try:
        form = await request.form()
    except Exception:
        raw = await request.body()
        try:
            from urllib.parse import parse_qs
            form = {k: v[0] if isinstance(v, list) else v
                    for k, v in parse_qs(raw.decode("utf-8", "ignore")).items()}
        except Exception:
            form = {}

    frm     = form.get("from", "")
    to_rcpt = form.get("to", "")
    subject = (form.get("subject", "") or "").strip() or "(제목 없음)"
    text    = (form.get("text", "") or "").strip() or "(내용 없음)"
    html    = form.get("html", None)

    # 첨부
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

    # 게이트웨이: 분류 → 스레드 기록 → (옵션)Run
    try:
        tag = classify_email(frm, subject, text)
        imp = importance_score(tag, subject, text)
        push_to_thread_and_maybe_run(tag, frm, to_rcpt, subject, text)
    except Exception:
        pass

    return {"ok": True}

# 콘솔에 /inbound/sendgrid 로 잡아둔 경우 호환용
@app.post("/inbound/sendgrid")
async def inbound_alias(request: Request, token: str):
    return await inbound_parse(request, token)


# ====== Inbox (list / view / attach) ======
@app.get("/inbox", response_class=HTMLResponse)
def inbox(limit: int = 10, token: Optional[str] = None, authorization: Optional[str] = Header(None)):
    if not _authorized(token, authorization):
        return HTMLResponse("<h3>401 Unauthorized</h3>", status_code=401)
    rows = list_messages_since(None, limit)
    rows_html = "".join([
        f"<li><b>{r.get('subject','')}</b><br/>From: {r.get('from','')}<br/>To: {r.get('to','')}<br/>"
        f"<pre style='white-space:pre-wrap'>{(r.get('text','') or '')[:1000]}</pre>"
        f"<div>첨부: {'있음' if r.get('has_attachments') else '없음'} "
        f"(보기: /mail/view?id={r.get('id')}&token={token or ''})</div><hr/></li>"
        for r in rows
    ]) or "<li>(수신 없음)</li>"
    return HTMLResponse(f"<html><body><h2>최근 수신 {limit}개</h2><ul>{rows_html}</ul></body></html>")

@app.get("/inbox.json")
def inbox_json(limit: int = 10, token: Optional[str] = None, authorization: Optional[str] = Header(None)):
    if not _authorized(token, authorization):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    rows = list_messages_since(None, limit)
    return {"ok": True, "messages": rows}

@app.get("/mail/view")
def mail_view(id: int, token: Optional[str] = None, authorization: Optional[str] = Header(None)):
    """단건 조회: 본문/HTML/첨부(메타)까지 반환"""
    if not _authorized(token, authorization):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    msg = get_message_by_id(id)
    if not msg:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return {"ok": True, "message": msg}

@app.get("/mail/attach")
def mail_attach(
    id: int, idx: int = 0, download: int = 1,
    token: Optional[str] = None, authorization: Optional[str] = Header(None)
):
    """첨부 다운로드: /mail/attach?id=123&idx=0&download=1"""
    if not _authorized(token, authorization):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    msg = get_message_by_id(id)
    if not msg:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    atts = msg.get("attachments") or []
    if not atts or idx < 0 or idx >= len(atts):
        return JSONResponse({"ok": False, "error": "attachment not found"}, status_code=404)
    att = atts[idx]
    filename = att.get("filename") or f"attach-{idx}.bin"
    b64 = att.get("content_b64") or ""
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid attachment"}, status_code=400)
    headers = {}
    if int(download or 0) == 1:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(content=raw, media_type="application/octet-stream", headers=headers)
