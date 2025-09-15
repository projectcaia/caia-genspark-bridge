# server/utils/telegram_notify.py
import os, requests

MAIL_BASE = os.getenv("MAIL_BASE", "https://worker-production-4369.up.railway.app")
AUTH_TOKEN = os.getenv("AUTH_TOKEN")

def send_telegram_message(text: str, subject: str="Caia Agent 알림"):
    url = f"{MAIL_BASE}/tool/send?token={AUTH_TOKEN}"
    payload = {
        "to": ["telegram"],
        "subject": subject,
        "text": text
    }
    r = requests.post(url, json=payload, timeout=10)
    try:
        r.raise_for_status()
    except Exception as e:
        print("Telegram notify failed:", e)


def send_approval_request(mail_id: int, sender: str, subject: str):
    """Send a Telegram message asking for mail approval with action buttons."""
    if not AUTH_TOKEN:
        return
    text = f"Mail #{mail_id}\nFrom: {sender}\nSubject: {subject}"
    approve = f"{MAIL_BASE}/mail/approve?id={mail_id}&token={AUTH_TOKEN}"
    reject = f"{MAIL_BASE}/mail/reject?id={mail_id}&token={AUTH_TOKEN}"
    payload = {
        "to": ["telegram"],
        "subject": "Mail approval needed",
        "text": text,
        "buttons": [
            {"text": "Approve", "url": approve},
            {"text": "Reject", "url": reject},
        ],
    }
    try:
        requests.post(f"{MAIL_BASE}/tool/send?token={AUTH_TOKEN}", json=payload, timeout=10)
    except Exception as e:
        print("Telegram approval request failed:", e)
