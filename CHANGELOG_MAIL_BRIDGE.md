# Mail Bridge Fix – 2025-09-17 (Seoul)

## What changed
- **SendGrid-only sending**: Removed SMTP fallback path. If SendGrid fails, the API now returns `502` instead of silently falling back to SMTP.
- **Inbound body fix**: When SendGrid provides only `html` (no `text`), the server now auto-generates plain text from HTML and stores it as `text`. `/mail/view` will therefore always have a `text` field.
- **Health/Status clarity**: `smtp_user` is hidden to avoid confusion in SendGrid-only mode.
- **No code path to IMAP/Zoho**: Primary endpoint paths do not use IMAP. Legacy files remain but are not used by the app.

## Why
- Outbound was 202 at SendGrid but confusing SMTP fallback could mask issues.
- Inbound often contains only HTML; tools expecting `text` saw an empty body.

## Action items for deployment
1. **Remove Zoho/IMAP SMTP variables** from Railway (IMAP_HOST/PORT/USER, ZOHO_* and SMTP_*).
2. Confirm **Inbound Parse** points to:  
   `https://mail-bridge.up.railway.app/inbound/sen?token=INBOUND_TOKEN`
3. Ensure `SENDGRID_API_KEY` and `SENDER_DEFAULT` are set.
4. Test:
   - `POST /tool/send` with `{"to":["you@example.com"],"subject":"test","text":"hello"}`
   - Send an external email to `caia@caia-agent.com` and verify `/inbox.json`, `/mail/view?id=...`.
