# mailer_sg.py
import asyncio, os
from typing import List, Optional
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Cc, Bcc, Attachment, FileContent, FileName, FileType, Disposition

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
):
    subject = (subject or "").strip() or "(제목 없음)"
    text = (text or "").strip() or "(내용 없음)"

    msg = Mail(
        from_email=Email(mail_from),
        to_emails=[To(x) for x in to],
        subject=subject,
        plain_text_content=text,
        html_content=html if html else None,
    )
    if cc:  msg.cc  = [Cc(x) for x in cc]
    if bcc: msg.bcc = [Bcc(x) for x in bcc]
    if attachments_b64:
        msg.attachments = [
            Attachment(
                FileContent(att["content_b64"]),
                FileName(att["filename"]),
                FileType("application/octet-stream"),
                Disposition("attachment"),
            )
            for att in attachments_b64
        ]

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send_blocking, msg)

def _send_blocking(msg: Mail):
    sg = SendGridAPIClient(SG_API_KEY)
    resp = sg.send(msg)
    # 진단 로그
    try:
        print("[SendGrid] status:", resp.status_code)
        print("[SendGrid] body:", resp.body.decode() if hasattr(resp.body, "decode") else resp.body)
        print("[SendGrid] headers:", dict(resp.headers))
    except Exception:
        pass
    if int(resp.status_code) >= 400:
        raise RuntimeError(f"SendGrid error {resp.status_code}")
    return {"status": int(resp.status_code)}
