from typing import Callable, Optional

from server.utils.telegram_notify import send_telegram_message


def report_crit_error(message: str, fallback: Optional[Callable[[str], None]] = None) -> None:
    """Send a CRIT level alert via Telegram.

    Tries ``send_telegram_message`` first and falls back to ``fallback``
    callable (e.g., ``app.telegram_notify``) if provided.
    Any exceptions are swallowed to avoid cascading failures.
    """
    text = f"[CRIT] {message}"
    try:
        ok = send_telegram_message(text)
        if not ok and fallback:
            fallback(text)
    except Exception:
        if fallback:
            try:
                fallback(text)
            except Exception:
                pass
