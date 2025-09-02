# Caia MailBridge Extended Patch (2025-09-02)

## Features Added
1. **Mail Deletion**
   - `/mail/delete` endpoint: manually mark mail as deleted
   - Background task: auto-delete low priority mails older than TTL (default: 7 days)

2. **Mail Auto-Reply**
   - `/mail/auto-reply` endpoint: trigger auto-reply based on conditions
   - Background task: scans for mails needing reply, sends reply via `/mail/send`

3. **Telegram Notifications**
   - Uses existing `/tool/send` API to push notifications
   - Events: auto-delete, auto-reply, important mail report

4. **Database Extensions**
   - `priority` field (default=low)
   - `deleted` flag
   - `replied` flag
   - `created_at` timestamp

## How It Works
- When a new mail is received, it’s stored with metadata
- Background tasks run every hour:
  - Delete low-priority mails older than 7 days
  - Send auto-replies where applicable
  - Notify Telegram for each event

## Files
- `server/routes/mail_manage.py` – New endpoints for delete and auto-reply
- `server/tasks/auto_tasks.py` – Background loops for auto-delete and auto-reply
- `server/utils/telegram_notify.py` – Helper to send Telegram notifications via MailBridge `/tool/send`
- `README.md` – This file

## Deployment
1. Copy files into your MailBridge project
2. Ensure Railway env has:
   - `AUTH_TOKEN`
   - `INBOUND_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `TELEGRAM_BOT_ID` (if needed)
3. Start server, confirm `/status`
4. Test auto-delete/auto-reply with sample mails

