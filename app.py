# app.py
import os, base64, re, time, json
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from pydantic import BaseModel, Field
from mailer_sg import send_email_sg
from store import init_db, save_messages, list_messages_since

APP = FastAPI(title="Caia Mail Bridge – SendGrid")

# ── ENV
SENDER_DEFAULT = os.getenv("SENDER_DEFAULT")           # 예: axel.nam@caia-agent.com
INBOUND_TOKEN  = os.getenv("INBOUND_TOKEN")            # 예: 랜덤 토큰

# ── Schemas
class SendReq(BaseModel):
    to: List[str]
    subject: str
    text: str = Field("", description="plain text")
    html: Optional[str] = None
    cc: Optional[List[str]] = None
    bcc: Optional[List[str]] = None
    attachments_b64: Optional[List[dict]] = None  # [{"filename":"a.txt","content_b64":"..."}]

class NLReq(BaseModel):
    command: str
    default_to: Optional[List[str]] = None

# ── Startup
@APP.on_event("startup")
def on_startup():
    init_db()

# ── 발신(JSON)
@APP.post("/mail/send")
async def api_send(req: SendReq):
    subject = (req.subject or "").strip() or "(제목 없음)"
    text = (req.text or "").strip() or "(내용 없음)"
    await send_email_sg(
        mail_from=SENDER_DEFAULT, to=req.to, subject=subject,
        text=text, html=req.html, cc=req.cc, bcc=req.bcc,
        attachments_b64=req.attachments_b64
    )
    return {"ok": True}

# ── 발신(Form + 첨부)
@APP.post("/mail/send-form")
async def api_send_form(
    to: str = Form(...),
    subject: str = Form(""),
    text: str = Form(""),
    html: str = Form(None),
    files: List[UploadFile] = File(default_factory=list),
):
    subject = (subject or "").strip() or "(제목 없음)"
    text = (text or "").strip() or "(내용 없음)"
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
    cmd = (req.command or "").strip()
    to = req.default_to or []
    # 이메일 추출
    emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', cmd)
    if emails:
        to = list({*to, *emails})
    # 제목/내용 느슨한 파싱
    m_subj = re.search(r'(?:제목|subject)\s*(?:은|:)?\s*([^\n,]+)', cmd, flags=re.IGNORECASE)
    m_body = re.search(r'(?:내용|message|body)\s*(?:은|:)?\s*(.+)$', cmd, flags=re.IGNORECASE)
    subj = (m_subj.group(1).strip() if m_subj else "").strip().strip('"').strip("'")
    body = (m_body.group(1).strip() if m_body else "").strip()
    if not body:
        # 제목 뒤 꼬리부분에서 내용 추정
        if subj and subj in cmd:
            tail = cmd.split(subj, 1)[-1]
            m2 = re.search(r'(?:내용|보내)\s*(?:은|를|:)?\s*(.+)$', tail)
            if m2:
                body = m2.group(1).strip()
    # 기본값
    subj = subj or "(제목 없음)"
    body = body or "(내용 없음)"
    if not to:
        return {"ok": False, "error": "받는사람 이메일이 필요해. 'to: user@example.com' 포함해줘."}
    await send_email_sg(mail_from=SENDER_DEFAULT, to=to, subject=subj, text=body)
    return {"ok": True, "to": to, "subject": subj}

# ── 수신(Webhook: SendGrid Inbound Parse)
@APP.post("/mail/inbound")
async def inbound_parse(request: Request, token: str):
    if INBOUND_TOKEN and token != INBOUND_TOKEN:
        raise HTTPException(401, "invalid token")

    form = await request.form()
    frm     = form.get("from", "")
    to_rcpt = form.get("to", "")
    subject = (form.get("subject", "") or "").strip() or "(제목 없음)"
    text    = (form.get("text", "") or "").strip() or "(내용 없음)"
    html    = form.get("html", None)

    # 첨부
    attachments = []
    try:
        n = int(form.get("attachments", 0))
    except:
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
    return {"ok": True}

# ── SendGrid 목적지 URL이 /inbound/sendgrid 인 경우 호환용 alias
@APP.post("/inbound/sendgrid")
async def inbound_alias(request: Request, token: str):
    return await inbound_parse(request, token)

# ── 새 메일 조회
@APP.get("/mail/new")
async def api_new(since_id: Optional[int] = None, limit: int = 20):
    rows = list_messages_since(since_id, limit)
    return {"ok": True, "messages": rows}

# ── 호환용: 윈도우/셸 JSON 파싱 이슈 우회 (raw body 직접 처리)
@APP.post("/mail/send-raw")
async def api_send_raw(request: Request):
    raw = await request.body()
    try:
        data = json.loads(raw.decode("utf-8", "ignore"))
    except Exception as e:
        return {"ok": False, "parse_error": str(e), "raw_sample": raw[:120].decode("utf-8", "ignore")}
    to = data.get("to") or []
    subject = (data.get("subject") or "").strip() or "(제목 없음)"
    text = (data.get("text") or "").strip() or "(내용 없음)"
    html = data.get("html")
    cc = data.get("cc")
    bcc = data.get("bcc")
    atts = data.get("attachments_b64")
    if not to:
        return {"ok": False, "error": "to가 필요합니다."}
    await send_email_sg(
        mail_from=SENDER_DEFAULT, to=to, subject=subject,
        text=text, html=html, cc=cc, bcc=bcc, attachments_b64=atts
    )
    return {"ok": True}

@APP.post("/mail/nl-raw")
async def api_nl_raw(request: Request):
    raw = await request.body()
    try:
        data = json.loads(raw.decode("utf-8", "ignore"))
        command = data.get("command", "")
    except Exception:
        command = raw.decode("utf-8", "ignore")

    emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', command)
    to = list(set(emails))

    m_subj = re.search(r'(?:제목|subject)\s*(?:은|:)?\s*([^\n,]+)', command, flags=re.IGNORECASE)
    m_body = re.search(r'(?:내용|message|body)\s*(?:은|:)?\s*(.+)$', command, flags=re.IGNORECASE)
    subj = (m_subj.group(1).strip() if m_subj else "")
    body = (m_body.group(1).strip() if m_body else "")

    if not body and subj and subj in command:
        tail = command.split(subj, 1)[-1]
        m2 = re.search(r'(?:내용|보내)\s*(?:은|를|:)?\s*(.+)$', tail)
        if m2:
            body = m2.group(1).strip()

    subj = subj.strip().strip('"').strip("'") or "(제목 없음)"
    body = body or "(내용 없음)"
    if not to:
        return {"ok": False, "error": "받는사람 이메일 필요. 'to: user@example.com' 포함해줘."}
    await send_email_sg(mail_from=SENDER_DEFAULT, to=to, subject=subj, text=body)
    return {"ok": True, "to": to, "subject": subj}

# ── 디버그: 수신 바디 확인용
@APP.post("/debug/echo")
async def debug_echo(request: Request):
    raw = await request.body()
    return {"len": len(raw), "raw": raw.decode("utf-8", "ignore")}

# ── 헬스체크
@APP.get("/health")
async def health():
    return {"ok": True, "sender": SENDER_DEFAULT}
