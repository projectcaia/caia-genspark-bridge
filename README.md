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

## Manual Management API
The manual mail management endpoints require authentication. Supply the API
token via a `token` query parameter or an `Authorization: Bearer <token>`
header.

Example:

```bash
curl -X POST \
  'https://<HOST>/mail/delete?token=YOUR_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"id": 1}'
```

or

```bash
curl -X POST \
  'https://<HOST>/mail/auto-reply' \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"id": 1, "reply_text": "Thanks"}'
```

## Mail Approval Flow
Certain inbound mails are not processed automatically. If a message has
attachments, comes from a pre-defined sender list or has high importance it is
stored with `needs_approval=1` and a Telegram message is sent with **Approve**
and **Reject** buttons. Each button links to `/mail/approve` or `/mail/reject`
with the mail ID.

When an operator approves, the server marks the mail as approved/processed,
saves any attachments to disk and sends a short confirmation reply. Rejecting
removes the mail from the database. This review step helps ensure that only
trusted messages trigger automated actions.
