# OmniAI Email Shooter

Self-hosted local bulk email tool for lawful, consent-based sending. The entire
UI is a single chat page driven by an AI assistant — uploads, sender config,
campaign drafting, send-to-many, and validation all happen through chat.

## Run

```powershell
python run_ui.py
```

Open: `http://127.0.0.1:5173/`

(Override the port with `$env:OMNIAI_UI_PORT="5174"` before launching.)

## AI mode (recommended) vs Local mode

Two backends drive the chat:

| Mode | Trigger | Brain | Capabilities |
|---|---|---|---|
| **AI** | `ANTHROPIC_API_KEY` set in env | Claude Opus 4.7 with adaptive thinking + tool calling + server-side `web_search` | Free-form natural language. The model picks tools to run. |
| **Local** | No key set | Rule-based regex router (built-in) | A fixed command vocabulary (`help`, `dryrun`, `send to ...`, `template newsletter`, etc.) |

A badge in the chat header shows which mode is active. Both modes hit the same
underlying actions — every tool the AI can call also has a regex command.

### Enable AI mode

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python run_ui.py
```

Then reload the chat — the badge flips to **AI · claude-opus-4-7**.

Verify with a curl: `GET /api/chat-config` returns `{"enabled": true, ...}`.

The LLM has these tools at its disposal (auto-invoked from your prompts):
`status`, `list_recipients` (with consent filter), `add_contacts`,
`remove_contact`, `clear_contacts`, `inspect_contact`, `consent_breakdown`,
`suppress`/`unsuppress`, `use_dryrun`, `configure_gmail`, `test_connection`,
`delete_sender`, `new_campaign`, `set_field`, `apply_template`, `show_draft`,
`save_campaign`, `delete_campaign`, `test_send`, `bulk_send`, `progress`,
`send_to` (one-shot multi-recipient), plus `web_search` for live internet
lookups.

## The user journey

### Step 1 - upload recipients
- Drop a CSV/XLSX onto the dropzone, or click to pick a file.
- Required column: `email`. Optional: `first_name`, `last_name`, `company`, `consent_status`, `tags`.
- A toast shows `Imported X, updated Y, skipped Z`. The recipients then appear in the campaign panel below.

### Step 2 - compose campaign
- Tabs: **Compose** (subject + HTML + plain text + purpose + delay), **Recipients** (the list you just uploaded), **Compliance** (auto-populates after Save & validate).
- Available template variables: `{{first_name}} {{last_name}} {{company}} {{sender_name}} {{physical_address}} {{unsubscribe_url}}`.

### Step 3 - connect Gmail or SMTP (inline)
- Open the collapsible **"3. Configure Gmail or SMTP"** panel inside the Campaign card.
- Pick a provider tab: **Gmail / Custom SMTP / Local Mailpit / Dry Run**. Each tab shows the prerequisites it needs.
- For Gmail you must:
  1. Enable 2-Step Verification at https://myaccount.google.com/security
  2. Create an App Password at https://myaccount.google.com/apppasswords
  3. Paste the 16-character app password into the form (the normal Gmail password will NOT work).
- Click **Save & test connection**. A green `OK` confirms SMTP login.

Gmail defaults the panel applies automatically:

```
Host:       smtp.gmail.com
Port:       587
Encryption: STARTTLS
Username:   <your Gmail address>
```

### Step 4 - test and bulk send
- Click **Save & validate** (this also runs compliance checks).
- Type your own inbox into the **Test send** bar and click **Send test email**.
- When happy, click **Send to all eligible recipients**. A live progress bar updates as the background thread sends.

## Compliance guardrails (automatic)
- Truthful From + Reply-To
- Required physical address
- Required unsubscribe link in marketing/newsletter HTML (`{{unsubscribe_url}}`)
- Suppression list filtering (bounced / complained / unsubscribed)
- No marketing sends to `unknown` consent
- Sender daily limit
- Jittered per-recipient delay (set by `delay_seconds` in the campaign)

The app does NOT include: spoofing, fake headers, account rotation, provider-limit bypass, proxy rotation, CAPTCHA bypass, or scraping.

## Quick CSV format

```csv
email,first_name,last_name,company,consent_status,tags
alice@example.com,Alice,Walker,Acme,opted_in,newsletter
bob@example.com,Bob,Stone,Globex,opted_in,
```

Allowed `consent_status`: `opted_in, soft_opt_in, transactional, unknown, unsubscribed, bounced, complained`.

## Smoke test (dry-run, no real email)

A scripted end-to-end run that exercises upload -> sender -> campaign -> test -> bulk:

```powershell
python run_ui.py            # leave running
# in another shell:
python smoke_test.py        # uses dry-run sender, asserts everything green
```

## Test with your real Gmail

1. Start the server: `python run_ui.py`
2. Open `http://127.0.0.1:5173/`
3. Drop your CSV.
4. Expand **3. Configure Gmail or SMTP**, pick the **Gmail** tab, fill in your Gmail address + app password.
5. Click **Save & test connection** -- you should see `OK`.
6. Click **Save & validate**, then **Send test email** to your own inbox.
7. When the test email arrives, click **Send to all eligible recipients**.

Secrets stay in server memory only; nothing is written to disk except the public sender record (no password).
