# mailer.py
import asyncio
import ssl
import smtplib
import base64
import time
import mimetypes
from email.message import EmailMessage
from email.utils import make_msgid, formatdate
from typing import List, Optional, Dict, Any


async def send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    use_ssl: bool,
    mail_from: str,
    to: List[str],
    subject: str,
    text: str,
    html: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    attachments_b64: Optional[List[dict]] = None,
    # ── 추가 옵션(기존 호출부와 호환 유지)
    reply_to: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
    retries: int = 2,
    retry_backoff: float = 0.6,
) -> str:
    """
    SMTP 발신(SSL / STARTTLS 자동 처리), 재시도/타임아웃 포함.
    반환값: 생성한 Message-ID 문자열
    """

    # ── 수신자 목록 구성(BCC 포함, 중복 제거)
    rcpt_to = list(dict.fromkeys([*(to or []), *(cc or []), *(bcc or [])]))

    # ── 메시지 작성
    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = ", ".join(to or [])
    if cc:
        msg["Cc"] = ", ".join(cc)
    # BCC는 헤더에 넣지 않음
    msg["Subject"] = subject or ""
    msg["Date"] = formatdate(localtime=True)

    if reply_to:
        msg["Reply-To"] = reply_to

    if headers:
        for k, v in headers.items():
            # 보안상 민감 헤더는 무시 가능(필요시 화이트리스트 방식으로)
            if k.lower() not in {"from", "to", "cc", "bcc", "subject", "date", "reply-to"}:
                msg[k] = str(v)

    # 텍스트/HTML 본문
    plain = (text or "")
    if html:
        msg.set_content(plain)
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(plain)

    # 첨부파일 처리 (MIME 타입 유추)
    if attachments_b64:
        for att in attachments_b64:
            filename = att.get("filename", "attachment.bin")
            content_b64 = att.get("content_b64", "")
            try:
                content = base64.b64decode(content_b64)
            except Exception:
                # 손상된 베이스64인 경우 그냥 스킵
                continue
            ctype, enc = mimetypes.guess_type(filename)
            if ctype is None:
                maintype, subtype = "application", "octet-stream"
            else:
                maintype, subtype = ctype.split("/", 1)
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    # Message-ID 생성(전송 전 확정)
    message_id = make_msgid(domain=None)
    msg["Message-ID"] = message_id

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _send_blocking,
        smtp_host, smtp_port, smtp_user, smtp_pass,
        use_ssl, mail_from, rcpt_to, msg, timeout, retries, retry_backoff
    )
    return message_id


def _send_blocking(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    use_ssl: bool,
    mail_from: str,
    rcpt_to: List[str],
    msg: EmailMessage,
    timeout: int,
    retries: int,
    retry_backoff: float,
) -> None:
    """
    실제 블로킹 SMTP 전송부. 예외 시 재시도.
    """
    attempt = 0
    last_err: Optional[BaseException] = None

    while attempt <= retries:
        try:
            if use_ssl:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(host=smtp_host, port=smtp_port, timeout=timeout, context=context) as s:
                    _smtp_send_flow(s, smtp_user, smtp_pass, mail_from, rcpt_to, msg)
            else:
                with smtplib.SMTP(host=smtp_host, port=smtp_port, timeout=timeout) as s:
                    s.ehlo()
                    # STARTTLS 사용
                    context = ssl.create_default_context()
                    s.starttls(context=context)
                    s.ehlo()
                    _smtp_send_flow(s, smtp_user, smtp_pass, mail_from, rcpt_to, msg)
            return  # 성공 시 종료
        except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
            last_err = e
            if attempt == retries:
                raise
            # 지수 백오프
            time.sleep(max(0.1, retry_backoff * (2 ** attempt)))
            attempt += 1


def _smtp_send_flow(
    s: smtplib.SMTP,
    smtp_user: str,
    smtp_pass: str,
    mail_from: str,
    rcpt_to: List[str],
    msg: EmailMessage
) -> None:
    # 로그인(익명 전송 필요 시 빈 값으로 들어오면 스킵)
    if smtp_user:
        s.login(smtp_user, smtp_pass)

    # send_message가 헤더 인코딩/SMTPUTF8까지 적절히 처리
    # rcpt 옵션은 None이면 헤더 기반으로 자동
    s.send_message(msg, from_addr=mail_from, to_addrs=rcpt_to or None)
