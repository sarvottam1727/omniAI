# OmniAI Email Shooter

Self-hosted local bulk email campaign system for lawful, consent-based sending.

## Run

```powershell
cd C:\OmniAIEmailShooter
.\start_app.bat
```

Open:

```text
http://127.0.0.1:5173/
```

## Gmail Setup

1. Enable 2-Step Verification in the sender Gmail account.
2. Create a Google app password.
3. In the app, open `Sender`.
4. Click `Use Gmail preset`.
5. Enter:
   - From email: the Gmail address
   - SMTP username: the same Gmail address
   - SMTP password: the Google app password
6. Save sender ID.
7. Select it in `Campaign`.
8. Click `Test selected sender`.
9. Send a test email before sending a bulk campaign.

Gmail settings used by the app:

```text
Host: smtp.gmail.com
Port: 587
Encryption: STARTTLS
```

## Local Safe Demo

Use `Safe Dry Run` when you want to validate the full workflow without sending real emails.

Use `Local Mailpit/MailHog` if you have a local SMTP test server listening on `127.0.0.1:1025`.

## Recipient Upload Format

CSV/XLSX files must include:

```text
email
```

Optional columns:

```text
first_name,last_name,company,consent_status,tags
```

Allowed consent statuses:

```text
opted_in, soft_opt_in, transactional, unknown, unsubscribed, bounced, complained
```

Marketing/newsletter/sales/job/follow-up campaigns exclude:

```text
unknown, unsubscribed, bounced, complained
```

## Compliance Guardrails

The app requires or enforces:

- Truthful sender email and Reply-To
- Campaign purpose
- Physical address
- Unsubscribe link for marketing-style campaigns
- Suppression list checks
- No sends to bounced, complained or unsubscribed contacts
- No marketing sends to unknown-consent contacts
- Sender daily limit validation
- Per-recipient delay

The app does not include:

- Spoofing
- Fake headers
- Account rotation to evade limits
- Provider limit bypass
- Proxy rotation
- CAPTCHA bypass
- Scraping or harvested-list logic

## Test

```powershell
cd C:\OmniAIEmailShooter
.\run_tests.bat
```
