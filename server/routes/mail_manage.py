# server/routes/mail_manage.py
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
import sqlite3
import os
import requests

from app import require_token

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

    # SendGrid check
    sg_key = os.getenv("SENDGRID_API_KEY")
    if sg_key:
        try:
            r = requests.get(
                "https://api.sendgrid.com/v3/user/account",
                headers={"Authorization": f"Bearer {sg_key}"},
                timeout=5,
            )
            detail["sendgrid"] = (
                "ok" if r.status_code == 200 else f"error:{r.status_code}"
            )
        except Exception as e:
            detail["sendgrid"] = f"error:{e}"
    else:
        detail["sendgrid"] = "missing"

    # Telegram check
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{tg_token}/getMe", timeout=5
            )
            ok = r.status_code == 200 and r.json().get("ok")
            detail["telegram"] = "ok" if ok else f"error:{r.status_code}"
        except Exception as e:
            detail["telegram"] = f"error:{e}"
    else:
        detail["telegram"] = "missing"

    overall_ok = all(
        v in ("ok", "missing") for v in detail.values()
    )
    return {"ok": overall_ok, "detail": detail}

@router.post("/delete")
def mail_delete(
    req: DeleteRequest,
    token: str | None = Query(None),
    request: Request | None = None,
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
    token: str | None = Query(None),
    request: Request | None = None,
):
    require_token(token, request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE messages SET replied=1 WHERE id=?", (req.id,))
    conn.commit()
    return {"ok": True, "replied_id": req.id, "text": req.reply_text}
