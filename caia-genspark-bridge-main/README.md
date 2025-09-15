# Caia MailBridge Extended Patch (Conditional Notifications, 2025-09-02)

## Features
- Auto-delete low priority mails older than TTL (default: 7 days)
- Auto-reply based on conditions
- Telegram notifications only when **necessary**
  - High priority mail detected
  - Bulk auto-deletions (50+ mails at once)
  - Auto-reply failures

## Files
- `server/tasks/auto_tasks.py` – Background loops with conditional notifications
- `server/routes/mail_manage.py` – Endpoints for manual delete and auto-reply
- `server/utils/telegram_notify.py` – Telegram helper
