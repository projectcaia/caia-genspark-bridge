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
