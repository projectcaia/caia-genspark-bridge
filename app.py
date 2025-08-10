# app.py
import os, asyncio, base64, re
from typing import List, Optional
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form
from pydantic import BaseModel, Field
from mailer import send_email
from receiver import poll_once, ensure_imap_ok
from store import init_db, get_last_uid, set_last_uid, save_messages, list_messages_since, get_setting, set_setting

APP = FastAPI(title="Caia Mail Bridge v2")

# ── 환경설정 읽기
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.zoho.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "true").lower() == "true"

IMAP_HOST = os.getenv("IMAP_HOST", "imap.zoho.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER")
IMAP_PASS = os.getenv("IMAP_PASS")
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")
IMAP_POLL_SEC = int(os.getenv("IMAP_POLL_SEC", "45"))  # 30~120 추천

SENDER_DEFAULT = os.getenv("SENDER_DEFAULT", SMTP_USER)

class SendReq(BaseModel):
    to: List[str]
    subject: str
    text: str = Field(..., description="플레인 텍스트(필요시 html로 대체 가능)")
    html: Optional[str] = None
    cc: Optional[List[str]] = None
    bcc: Optional[List[str]] = None
    attachments_b64: Optional[List[dict]] = None  # [{"filename":"a.txt","content_b64":"..."}]

class NLReq(BaseModel):
    command: str
    default_to: Optional[List[str]] = None

class PullReq(BaseModel):
    force: bool = False

# ── 앱 부팅: DB 초기화 + 폴링 루프 시작
@app.on_event("startup")
async def on_startup():
    init_db()
    # 폴링 루프
    asyncio.create_task(poll_loop())

async def poll_loop():
    # 최초 연결 확인(실패해도 루프는 계속)
    await ensure_imap_ok(IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASS, IMAP_FOLDER)
    while True:
        try:
            last_uid = get_last_uid()
            new_last, msgs = await poll_once(
                IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASS, IMAP_FOLDER, last_uid
            )
            if msgs:
                save_messages(msgs)
            if new_last and new_last != last_uid:
                set_last_uid(new_last)
        except Exception as e:
            # 네트워크/IMAP 일시 오류는 다음 주기 재시도
            pass
        await asyncio.sleep(IMAP_POLL_SEC)

# ── 발신: JSON
@app.post("/mail/send")
async def api_send(req: SendReq):
    await send_email(
        smtp_host=SMTP_HOST, smtp_port=SMTP_PORT, smtp_user=SMTP_USER, smtp_pass=SMTP_PASS,
        use_ssl=SMTP_USE_SSL, mail_from=SENDER_DEFAULT, to=req.to, subject=req.subject,
        text=req.text, html=req.html, cc=req.cc, bcc=req.bcc, attachments_b64=req.attachments_b64
    )
    return {"ok": True}

# ── 발신: 폼 + 파일(첨부 용이)
@app.post("/mail/send-form")
async def api_send_form(
    to: str = Form(...),
    subject: str = Form(...),
    text: str = Form(""),
    html: str = Form(None),
    files: List[UploadFile] = File(default_factory=list),
):
    attachments_b64 = []
    for f in files:
        b = await f.read()
        attachments_b64.append({"filename": f.filename, "content_b64": base64.b64encode(b).decode()})
    await send_email(
        smtp_host=SMTP_HOST, smtp_port=SMTP_PORT, smtp_user=SMTP_USER, smtp_pass=SMTP_PASS,
        use_ssl=SMTP_USE_SSL, mail_from=SENDER_DEFAULT, to=[x.strip() for x in to.split(",") if x.strip()],
        subject=subject, text=text, html=html, attachments_b64=attachments_b64
    )
    return {"ok": True}

# ── 자연어 명령 → 발신
# 예) "카이아, 홍길동 <mail@ex.com>에게 제목은 회의요청, 내용은 내일 10시에 회의 가능하신가요 보내"
@app.post("/mail/nl")
async def api_nl(req: NLReq):
    to = req.default_to or []
    subj = None
    body = None

    # 간단 패턴: "제목은 X", "내용은 Y", "to: a@b.com"
    m_to = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', req.command)
    if m_to:
        to = list(set(to + m_to))

    m_subj = re.search(r'(제목|subject)\s*(은|:)\s*(.+?)(,|내용|보내|끝|$)', req.command)
    if m_subj:
        subj = m_subj.group(3).strip()

    m_body = re.search(r'(내용|message|body)\s*(은|:)\s*(.+)$', req.command)
    if m_body:
        body = m_body.group(3).strip()

    # fallback: "X에게 ... 보내" 구조
    if not subj:
        m = re.search(r'제목\s*(은|:)\s*(.+)$', req.command)
        if m: subj = m.group(2).strip()
    if not body:
        # 제목 다음 텍스트 전부를 본문으로 추정(허술하지만 실용)
        if subj:
            tail = req.command.split(subj, 1)[-1]
            m2 = re.search(r'(내용|보내)\s*(은|를|:)?\s*(.+)$', tail)
            if m2:
                body = m2.group(3).strip()
    if not body:
        # 최후: 전체 커맨드를 본문으로
        body = req.command

    if not to:
        return {"ok": False, "error": "받는사람 이메일이 필요해. 'to: user@example.com' 또는 커맨드에 이메일 포함해줘."}
    if not subj:
        subj = "(제목 없음)"

    await send_email(
        smtp_host=SMTP_HOST, smtp_port=SMTP_PORT, smtp_user=SMTP_USER, smtp_pass=SMTP_PASS,
        use_ssl=SMTP_USE_SSL, mail_from=SENDER_DEFAULT, to=to, subject=subj, text=body
    )
    return {"ok": True, "to": to, "subject": subj}

# ── 수신 강제 폴링 트리거(테스트용)
@app.post("/mail/pull")
async def api_pull(req: PullReq):
    last_uid = get_last_uid()
    new_last, msgs = await poll_once(
        IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASS, IMAP_FOLDER, None if req.force else last_uid
    )
    if msgs:
        save_messages(msgs)
    if new_last and new_last != last_uid:
        set_last_uid(new_last)
    return {"ok": True, "fetched": len(msgs)}

# ── 새 메일 조회
@app.get("/mail/new")
async def api_new(since_uid: Optional[int] = None, limit: int = 20):
    rows = list_messages_since(since_uid, limit)
    return {"ok": True, "messages": rows}

# ── 헬스체크
@app.get("/health")
async def health():
    return {"ok": True, "smtp_user": SMTP_USER, "imap_user": IMAP_USER}
