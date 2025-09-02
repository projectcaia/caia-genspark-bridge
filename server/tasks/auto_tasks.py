# server/tasks/auto_tasks.py
import sqlite3, os, requests
from datetime import datetime, timedelta

MAIL_BASE = os.getenv("MAIL_BASE", "https://worker-production-4369.up.railway.app")
AUTH_TOKEN = os.getenv("AUTH_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def get_db():
    conn = sqlite3.connect("mailbridge.sqlite3")
    conn.row_factory = sqlite3.Row
    return conn

def notify_telegram(subject: str, text: str):
    if not (AUTH_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"{MAIL_BASE}/tool/send?token={AUTH_TOKEN}"
    payload = {
        "to": ["telegram"],
        "subject": subject,
        "text": f"[CaiaMailBridge]\n{text}"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("Telegram notify failed", e)

def auto_delete(ttl_days: int = 7):
    conn = get_db()
    cur = conn.cursor()
    cutoff = datetime.utcnow() - timedelta(days=ttl_days)
    cur.execute("SELECT id, subject FROM mails WHERE deleted=0 AND priority='low' AND created_at < ?", (cutoff.isoformat(),))
    rows = cur.fetchall()
    for row in rows:
        cur.execute("UPDATE mails SET deleted=1 WHERE id=?", (row["id"],))
        notify_telegram("메일 자동삭제", f"메일 ID {row['id']} ({row['subject']}) 자동 삭제됨")
    conn.commit()

def auto_reply():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, from_, subject FROM mails WHERE replied=0 AND auto_reply=1")
    rows = cur.fetchall()
    for row in rows:
        notify_telegram("메일 자동답장", f"메일 ID {row['id']} ({row['subject']}) 자동 답장 보냄")
        cur.execute("UPDATE mails SET replied=1 WHERE id=?", (row["id"],))
    conn.commit()
