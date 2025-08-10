# mailer.py
import asyncio, ssl, smtplib, base64
from email.message import EmailMessage
from typing import List, Optional

async def send_email(
    smtp_host: str, smtp_port: int, smtp_user: str, smtp_pass: str,
    use_ssl: bool, mail_from: str, to: List[str], subject: str,
    text: str, html: Optional[str] = None, cc=None, bcc=None, attachments_b64=None
):
    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = ", ".join(to)
    if cc: msg["Cc"] = ", ".join(cc)
    if bcc:  # bcc는 헤더에 안넣지만, smtplib sendmail에서 받는사람 목록엔 포함
        to = to + bcc
    msg["Subject"] = subject

    if html:
        msg.set_content(text or "")
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(text or "")

    if attachments_b64:
        for att in attachments_b64:
            filename = att["filename"]
            content = base64.b64decode(att["content_b64"])
            msg.add_attachment(content, maintype="application", subtype="octet-stream", filename=filename)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_blocking, smtp_host, smtp_port, smtp_user, smtp_pass, use_ssl, mail_from, to, msg)

def _send_blocking(smtp_host, smtp_port, smtp_user, smtp_pass, use_ssl, mail_from, to, msg):
    if use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(mail_from, to, msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(smtp_user, smtp_pass)
            s.sendmail(mail_from, to, msg.as_string())
