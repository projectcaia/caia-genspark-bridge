# mailer_sg.py (final)
import asyncio, os, time, base64
from typing import List, Optional, Dict
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Email, To, Cc, Bcc, Attachment,
    FileContent, FileName, FileType, Disposition,
    ReplyTo, Category, MailSettings, SandBoxMode,
    TrackingSettings, ClickTracking, OpenTracking
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
    # ---- 추가 옵션 ----
    reply_to: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    categories: Optional[List[str]] = None,   # ex) ["COMMAND","ZENSPARK"]
    sandbox: bool = False,                    # True면 테스트(실발송 안됨)
    track_opens: bool = False,
    track_clicks: bool = False,
    retries: int = 2,
    backoff: float = 0.6
):
    if not SG_API_KEY:
        raise RuntimeError("SENDGRID_API_KEY not set")

    # 수신자 정리
    to  = [x.strip() for x in (to or []) if x and x.strip()]
    cc  = [x.strip() for x in (cc or []) if x and x.strip()] if cc else None
    bcc = [x.strip() for x in (bcc or []) if x and x.strip()] if bcc else None
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
    if cc:
        msg.cc = [Cc(x) for x in cc]
    if bcc:
        msg.bcc = [Bcc(x) for x in bcc]
    if reply_to:
        msg.reply_to = ReplyTo(reply_to)

    # 헤더
    if headers:
        # helpers.Header 대신 dict로 직접 지정이 호환성 높음
        msg.headers = {str(k): str(v) for k, v in headers.items()}

    # 카테고리
    if categories:
        # add_category()가 없는 버전 대비
        try:
            for c in categories:
                msg.add_category(Category(str(c)))
        except Exception:
            msg.category = [Category(str(c)) for c in categories]

    # 샌드박스/트래킹
    if sandbox or track_opens or track_clicks:
        ms = MailSettings()
        if sandbox:
            ms.sandbox_mode = SandBoxMode(enable=True)
        msg.mail_settings = ms

        ts = TrackingSettings()
        if track_clicks:
            ts.click_tracking = ClickTracking(enable=True, enable_text=True)
        if track_opens:
            ts.open_tracking = OpenTracking(enable=True)
        msg.tracking_settings = ts

    # 첨부 (content_b64 필수)
    if attachments_b64:
        atts = []
        for att in attachments_b64:
            content_b64 = att.get("content_b64") or att.get("content")  # 호환 키
            if not content_b64:
                continue
            # 이미 b64라면 그대로, raw bytes가 온 경우 b64로 인코딩
            if isinstance(content_b64, (bytes, bytearray)):
                content_b64 = base64.b64encode(content_b64).decode()
            atts.append(
                Attachment(
                    FileContent(content_b64),
                    FileName(att.get("filename", "attachment.bin")),
                    FileType(att.get("content_type", "application/octet-stream")),
                    Disposition("attachment"),
                )
            )
        if atts:
            msg.attachments = atts

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
            headers = dict(resp.headers) if resp.headers else {}
            msg_id = headers.get("X-Message-Id") or headers.get("X-Message-ID") or headers.get("x-message-id")

            # 디버그 로그(필요시 주석)
            try:
                print("[SendGrid] status:", status)
                body = resp.body.decode() if hasattr(resp.body, "decode") else resp.body
                if body:
                    print("[SendGrid] body:", body)
                print("[SendGrid] headers:", headers)
            except Exception:
                pass

            if status >= 400:
                # 재시도 케이스
                if status in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(max(0.1, backoff * (2 ** attempt)))
                    attempt += 1
                    continue
                raise RuntimeError(f"SendGrid error {status}")
            return {"status": status, "message_id": msg_id}
        except Exception as e:
            last_exc = e
            if attempt < retries:
                time.sleep(max(0.1, backoff * (2 ** attempt)))
                attempt += 1
                continue
            raise last_exc
