# server/routes/mail_manage.py
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
import sqlite3

from app import require_token

router = APIRouter()

class DeleteRequest(BaseModel):
    id: int

class AutoReplyRequest(BaseModel):
    id: int
    reply_text: str | None = "자동 회신: 메일을 확인했습니다."

def get_db():
    conn = sqlite3.connect("mailbridge.sqlite3")
    conn.row_factory = sqlite3.Row
    return conn

@router.post("/mail/delete")
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

@router.post("/mail/auto-reply")
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
