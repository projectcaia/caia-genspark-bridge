# server/routes/mail_manage.py
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
import sqlite3
import os
import ssl
import smtplib
import imaplib

from app import require_token, telegram_notify
from server.utils.error_report import report_crit_error

router = APIRouter(prefix="/mail")

class DeleteRequest(BaseModel):
    id: int

class AutoReplyRequest(BaseModel):
    id: int
    reply_text: str | None = "자동 회신: 메일을 확인했습니다."

def get_db():
    conn = sqlite3.connect("mailbridge.sqlite3")
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/health")
def mail_health():
    detail: dict[str, str] = {}

    # DB check
    try:
        conn = get_db()
        conn.execute("SELECT 1")
        detail["db"] = "ok"
    except Exception as e:
        detail["db"] = f"db_error: {e}"
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # SMTP check
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "0")) if os.getenv("SMTP_PORT") else None
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD") or os.getenv("ZOHO_SMTP_PASSWORD")
    if smtp_host and smtp_port and smtp_user and smtp_pass:
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=5) as s:
                s.login(smtp_user, smtp_pass)
            detail["smtp"] = "ok"
        except Exception as e:
            detail["smtp"] = f"error:{e}"
    else:
        detail["smtp"] = "missing"

    # IMAP check
    imap_host = os.getenv("IMAP_HOST")
    imap_port = int(os.getenv("IMAP_PORT", "0")) if os.getenv("IMAP_PORT") else None
    imap_user = os.getenv("IMAP_USER")
    imap_pass = os.getenv("IMAP_PASSWORD") or os.getenv("ZOHO_IMAP_PASSWORD")
    if imap_host and imap_port and imap_user and imap_pass:
        try:
            with imaplib.IMAP4_SSL(imap_host, imap_port) as imap:
                imap.login(imap_user, imap_pass)
            detail["imap"] = "ok"
        except Exception as e:
            detail["imap"] = f"error:{e}"
    else:
        detail["imap"] = "missing"

    overall_ok = all(v == "ok" for v in detail.values())
    if not overall_ok:
        report_crit_error(f"mail_health failed: {detail}", telegram_notify)
        return {"ok": False, "detail": detail}
    return {"ok": True}

@router.post("/delete")
def mail_delete(
    req: DeleteRequest,
    request: Request,
    token: str | None = Query(None),
):
    require_token(token, request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE messages SET deleted=1 WHERE id=?", (req.id,))
    conn.commit()
    return {"ok": True, "deleted_id": req.id}

@router.post("/auto-reply")
def auto_reply(
    req: AutoReplyRequest,
    request: Request,
    token: str | None = Query(None),
):
    require_token(token, request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE messages SET replied=1 WHERE id=?", (req.id,))
    conn.commit()
    return {"ok": True, "replied_id": req.id, "text": req.reply_text}
