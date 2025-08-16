# mailer_sg.py (보강판)
import asyncio, os, time
from typing import List, Optional, Dict
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Email, To, Cc, Bcc, Attachment,
    FileContent, FileName, FileType, Disposition,
    ReplyTo, Header
)

SG_API_KEY = os.getenv("SENDGRID_API_KEY")

async def send_email_sg(
    mail_from: str,
    to: List[str],
    subject: str,
    text: str,
    html: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    attachments_b64: Optional[List[dict]] = None,
    # ---- 추가 옵션 (기존 호출과 호환) ----
    reply_to: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    categories: Optional[List[str]] = None,   # ex) ["COMMAND","ZENSPARK"]
    sandbox: bool = False,                    # True면 테스트(실발송 안됨)
    track_opens: bool = False,
    track_clicks: bool = False,
    retries: int = 2,                         # 429/5xx 재시도
    backoff: float = 0.6
):
    if not SG_API_KEY:
        raise RuntimeError("SENDGRID_API_KEY not set")

    # 수신자 정리(중복 제거/빈 제거)
    to   = [x.strip() for x in (to or []) if x and x.strip()]
    cc   = [x.strip() for x in (cc or []) if x and x.strip()] if cc else None
    bcc  = [x.strip() for x in (bcc or []) if x and x.strip()] if bcc else None
    if not to:
        raise ValueError("to recipients required")

    subject = (subject or "").strip() or "(제목 없음)"
    text    = (text or "").strip() or "(내용 없음)"

    msg = Mail(
        from_email=Email(mail_from),
        to_emails=[To(x) for x in to],
        subject=subject,
        plain_text_content=text,
        html_content=html if html else None,
    )
    if cc:  msg.cc  = [Cc(x) for x in cc]
    if bcc: msg.bcc = [Bcc(x) for x in bcc]

    if reply_to:
        msg.reply_to = ReplyTo(reply_to)

    # 선택 헤더
    if headers:
        for k, v in headers.items():
            msg.add_header(Header(k, str(v)))

    # 카테고리(대시보드 분류/검색용)
    if categories:
        for c in categories:
            msg.add_category(c)

    # 추적/샌드박스
    if sandbox:
        msg.mail_settings = msg.mail_settings or {}
        msg.mail_settings["sandbox_mode"] = {"enable": True}
    if (track_opens or track_clicks):
        msg.tracking_settings = msg.tracking_settings or {}
        if track_opens:
            msg.tracking_settings["open_tracking"] = {"enable": True}
        if track_clicks:
            msg.tracking_settings["click_tracking"] = {"enable": True, "enable_text": True}

    # 첨부
    if attachments_b64:
        msg.attachments = [
            Attachment(
                FileContent(att["content_b64"]),
                FileName(att.get("filename", "attachment.bin")),
                FileType(att.get("content_type", "application/octet-stream")),
                Disposition("attachment"),
            )
            for att in attachments_b64
        ]

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send_blocking, msg, retries, backoff)


def _send_blocking(msg: Mail, retries: int, backoff: float):
    sg = SendGridAPIClient(SG_API_KEY)

    attempt = 0
    last_exc = None
    while attempt <= retries:
        try:
            resp = sg.send(msg)
            status = int(resp.status_code)
            # 메시지 ID 추출(헤더 이름은 환경에 따라 다를 수 있음)
            headers = dict(resp.headers) if resp.headers else {}
            msg_id = headers.get("X-Message-Id") or headers.get("X-Message-ID") or headers.get("x-message-id")

            # 디버그(필요 시 주석)
            try:
                print("[SendGrid] status:", status)
                if hasattr(resp.body, "decode"):
                    print("[SendGrid] body:", resp.body.decode())
                print("[SendGrid] headers:", headers)
            except Exception:
                pass

            if status >= 400:
                # 4xx/5xx 처리
                if status in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(max(0.1, backoff * (2 ** attempt)))
                    attempt += 1
                    continue
                raise RuntimeError(f"SendGrid error {status}")
            return {"status": status, "message_id": msg_id}
        except Exception as e:
            last_exc = e
            # 네트워크/SDK 예외도 재시도
            if attempt < retries:
                time.sleep(max(0.1, backoff * (2 ** attempt)))
                attempt += 1
                continue
            raise last_exc
