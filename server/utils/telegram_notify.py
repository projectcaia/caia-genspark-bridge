# server/utils/telegram_notify.py
import os
import requests
from typing import Dict, Any

MAIL_BASE = (os.getenv("MAIL_BASE", "https://worker-production-4369.up.railway.app") or "").rstrip("/")
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")  # worker 측 인증 토큰
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "10"))

def _endpoint(path: str) -> str:
    return f"{MAIL_BASE}{path}?token={AUTH_TOKEN}"

def _post_json(path: str, payload: Dict[str, Any]) -> bool:
    if not AUTH_TOKEN:
        print("Telegram request skipped: AUTH_TOKEN missing.")
        return False
    try:
        r = requests.post(_endpoint(path), json=payload, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"Telegram request failed: {e}")
        return False

def send_telegram_message(text: str, subject: str = "Caia Agent 알림") -> bool:
    payload = {
        "to": ["telegram"],
        "subject": subject,
        "text": str(text or "")
    }
    return _post_json("/tool/send", payload)

def send_approval_request(mail_id: int, sender: str, subject: str) -> bool:
    """승인/거부 요청 메시지 전송 (버튼 + 답장 가이드 동시 제공)."""
    if not AUTH_TOKEN:
        print("Approval request skipped: AUTH_TOKEN missing.")
        return False

    text = f"Mail #{mail_id}\nFrom: {sender}\nSubject: {subject}"
    approve = f"{MAIL_BASE}/mail/approve?id={mail_id}&token={AUTH_TOKEN}"
    reject  = f"{MAIL_BASE}/mail/reject?id={mail_id}&token={AUTH_TOKEN}"

    payload = {
        "to": ["telegram"],
        "subject": "Mail approval needed",
        "text": text + "\n\n이 메시지에 '승인' 또는 '거부'로 답장해도 처리됩니다.",
        "buttons": [
            {"text": "Approve", "url": approve},
            {"text": "Reject",  "url": reject},
        ],
    }

    ok = _post_json("/tool/send", payload)
    if not ok:
        # 버튼 실패 시 폴백: 순수 텍스트 안내
        fallback = text + "\n\n(버튼 전송 실패) '승인' 또는 '거부'로 답장해주세요."
        _post_json("/tool/send", {"to": ["telegram"], "subject": "Mail approval needed", "text": fallback})
    return ok
