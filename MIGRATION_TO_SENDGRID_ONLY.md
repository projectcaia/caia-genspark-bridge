# Migration: Zoho → SendGrid only

## Remove from Railway ENV
```
IMAP_HOST, IMAP_PORT, IMAP_USER, ZOHO_IMAP_PASSWORD, IMAP_SECURE,
SMTP_HOST, SMTP_PORT, SMTP_USER, ZOHO_SMTP_PASSWORD, SMTP_SSL, SMTP_PASSWORD
```

## Keep
```
AUTH_TOKEN, INBOUND_TOKEN, DB_PATH, SENDER_DEFAULT,
SENDGRID_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
OPENAI_API_KEY, ASSISTANT_ID, THREAD_ID, AUTO_RUN, MAIL_BASE,
ALERT_CLASSES, ALERT_IMPORTANCE_MIN, APPROVAL_IMPORTANCE_MIN,
APPROVAL_SENDERS, SUBJECT_PREFIX, ZENSPARK_INBOX, POLL_INTERVAL_SEC, HTTP_TIMEOUT
```

## SendGrid Inbound Parse
- Domain: `caia-agent.com`
- Endpoint: `https://mail-bridge.up.railway.app/inbound/sen?token=INBOUND_TOKEN`
- Options: **Do not** post raw MIME; use default fields (`from`, `to`, `subject`, `text`, `html`, `attachments[]`).

## Notes
- If an email has only HTML, the server auto-generates `text` via HTML stripping to keep tools compatible.
- `/tool/send` requires `"to"` as an array of emails. A plain string will return 422 by design.
