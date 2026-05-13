from __future__ import annotations

import base64
import csv
import html
import io
import json
import os
import re
import smtplib
import ssl
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from email.message import EmailMessage
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree
import cgi


ROOT = Path(__file__).parent / "frontend" / "static"
DATA_DIR = Path(__file__).parent / "local_data"
STATE_FILE = DATA_DIR / "state.json"
PORT = int(os.environ.get("OMNIAI_UI_PORT", "5173"))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MARKETING_TYPES = {"marketing", "newsletter", "sales_outreach", "job_outreach", "follow_up"}
ALLOWED_CONSENT = {"opted_in", "soft_opt_in", "transactional"}
BLOCKED_CONSENT = {"unsubscribed", "bounced", "complained"}
SECRET_STORE: dict[str, str] = {}
STATE_LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed_state() -> dict:
    return {
        "senders": [
            {
                "id": "local-mailpit",
                "label": "Local Mailpit / MailHog",
                "provider": "mailpit",
                "sender_name": "Sarvottam Team",
                "sender_email": "dev@omniai.local",
                "reply_to": "support@omniai.local",
                "physical_address": "123 Compliance Street, Pune, India",
                "host": "127.0.0.1",
                "port": 1025,
                "username": "",
                "encryption": "none",
                "daily_limit": 500,
                "hourly_limit": 100,
                "password_configured": False,
                "created_at": now(),
            },
            {
                "id": "local-dryrun",
                "label": "Safe Dry Run",
                "provider": "dryrun",
                "sender_name": "Sarvottam Team",
                "sender_email": "dryrun@omniai.local",
                "reply_to": "support@omniai.local",
                "physical_address": "123 Compliance Street, Pune, India",
                "host": "dryrun.local",
                "port": 0,
                "username": "",
                "encryption": "none",
                "daily_limit": 500,
                "hourly_limit": 100,
                "password_configured": False,
                "created_at": now(),
            }
        ],
        "contacts": [
            {
                "id": uuid.uuid4().hex,
                "email": "aisha@example.com",
                "first_name": "Aisha",
                "last_name": "Mehta",
                "company": "BrightPath",
                "source": "seed",
                "consent_status": "opted_in",
                "tags": ["newsletter"],
                "created_at": now(),
            },
            {
                "id": uuid.uuid4().hex,
                "email": "unknown@example.com",
                "first_name": "Unknown",
                "last_name": "Lead",
                "company": "Atlas",
                "source": "seed",
                "consent_status": "unknown",
                "tags": ["lead"],
                "created_at": now(),
            },
            {
                "id": uuid.uuid4().hex,
                "email": "bounce@example.com",
                "first_name": "Old",
                "last_name": "Contact",
                "company": "Contoso",
                "source": "seed",
                "consent_status": "bounced",
                "tags": [],
                "created_at": now(),
            },
        ],
        "campaigns": [],
        "suppression": [
            {"id": uuid.uuid4().hex, "email": "bounce@example.com", "reason": "bounced", "source": "seed", "created_at": now()}
        ],
        "events": [],
    }


def load_state() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not STATE_FILE.exists():
        state = seed_state()
        save_state(state)
        return state
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    sender_ids = {sender.get("id") for sender in state.get("senders", [])}
    if "local-dryrun" not in sender_ids:
        state["senders"].append(seed_state()["senders"][1])
        save_state(state)
    return state


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def public_sender(sender: dict) -> dict:
    clean = dict(sender)
    clean.pop("password", None)
    clean["password_configured"] = bool(SECRET_STORE.get(sender["id"]))
    return clean


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(normalize_email(value)))


def read_json(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length) if length else b"{}"
    return json.loads(raw.decode("utf-8") or "{}")


def rows_from_csv(blob: bytes) -> list[dict]:
    text = blob.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "email" not in [name.strip().lower() for name in reader.fieldnames]:
        raise ValueError("CSV must contain an email column")
    return [{(key or "").strip().lower(): value for key, value in row.items()} for row in reader]


def xlsx_shared_strings(zip_file: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(zip_file.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values = []
    for item in root.findall("a:si", ns):
        texts = [node.text or "" for node in item.findall(".//a:t", ns)]
        values.append("".join(texts))
    return values


def cell_value(cell, shared: list[str]) -> str:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    value = cell.find("a:v", ns)
    if value is None:
        inline = cell.find(".//a:t", ns)
        return inline.text if inline is not None and inline.text else ""
    raw = value.text or ""
    if cell.attrib.get("t") == "s":
        index = int(raw)
        return shared[index] if index < len(shared) else ""
    return raw


def rows_from_xlsx(blob: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        shared = xlsx_shared_strings(zf)
        root = ElementTree.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = []
    for row in root.findall(".//a:row", ns):
        values = [cell_value(cell, shared).strip() for cell in row.findall("a:c", ns)]
        if any(values):
            rows.append(values)
    if not rows:
        return []
    headers = [value.strip().lower() for value in rows[0]]
    if "email" not in headers:
        raise ValueError("Excel file must contain an email column")
    records = []
    for row in rows[1:]:
        if not any(row):
            continue
        records.append({headers[i]: row[i] if i < len(row) else "" for i in range(len(headers)) if headers[i]})
    return records


def upsert_contacts(state: dict, rows: list[dict], source: str) -> dict:
    by_email = {contact["email"]: contact for contact in state["contacts"]}
    seen = set()
    imported = updated = skipped = 0
    errors = []
    for index, row in enumerate(rows, start=2):
        email = normalize_email(str(row.get("email", "")))
        if not email:
            skipped += 1
            errors.append({"row": index, "reason": "missing email"})
            continue
        if not valid_email(email):
            skipped += 1
            errors.append({"row": index, "email": email, "reason": "invalid email"})
            continue
        if email in seen:
            skipped += 1
            errors.append({"row": index, "email": email, "reason": "duplicate in file"})
            continue
        seen.add(email)
        consent = (row.get("consent_status") or "unknown").strip().lower()
        if consent not in {"opted_in", "soft_opt_in", "transactional", "unknown", "unsubscribed", "bounced", "complained"}:
            consent = "unknown"
        contact = by_email.get(email)
        created = contact is None
        if created:
            contact = {"id": uuid.uuid4().hex, "email": email, "created_at": now()}
            state["contacts"].append(contact)
        contact.update(
            {
                "email": email,
                "first_name": row.get("first_name") or row.get("name") or "",
                "last_name": row.get("last_name") or "",
                "company": row.get("company") or "",
                "source": source,
                "consent_status": consent,
                "tags": [tag.strip() for tag in str(row.get("tags") or "").split(",") if tag.strip()],
            }
        )
        imported += 1 if created else 0
        updated += 0 if created else 1
    return {"imported": imported, "updated": updated, "skipped": skipped, "errors": errors}


def validate_campaign(state: dict, campaign: dict) -> dict:
    sender = next((item for item in state["senders"] if item["id"] == campaign.get("sender_id")), None)
    suppression = {item["email"]: item["reason"] for item in state["suppression"]}
    eligible = []
    exclusions = []
    seen = set()
    for contact in state["contacts"]:
        email = contact["email"]
        reason = ""
        if email in seen:
            reason = "duplicate"
        elif not valid_email(email):
            reason = "invalid email"
        elif email in suppression:
            reason = f"suppression list: {suppression[email]}"
        elif contact.get("consent_status") in BLOCKED_CONSENT:
            reason = contact.get("consent_status")
        elif campaign.get("campaign_type") in MARKETING_TYPES and contact.get("consent_status") not in ALLOWED_CONSENT:
            reason = "no consent"
        seen.add(email)
        if reason:
            exclusions.append({"email": email, "reason": reason})
        else:
            eligible.append(contact)

    html_body = campaign.get("html_body", "")
    plain_body = campaign.get("plain_body", "")
    subject = campaign.get("subject", "")
    checks = [
        {"key": "sender", "ok": bool(sender), "severity": "error", "message": "Select the sender ID/account to send from."},
        {"key": "smtp", "ok": bool(sender and (sender.get("provider") == "dryrun" or (sender.get("host") and sender.get("port") is not None))), "severity": "error", "message": "SMTP/Gmail host and port are required."},
        {"key": "from", "ok": bool(sender and sender.get("sender_email") and valid_email(sender.get("sender_email"))), "severity": "error", "message": "Truthful From email is required."},
        {"key": "physical_address", "ok": bool(sender and sender.get("physical_address")), "severity": "error", "message": "Physical address is required."},
        {"key": "unsubscribe", "ok": ("{{unsubscribe_url}}" in html_body or "unsubscribe" in html_body.lower()), "severity": "error" if campaign.get("campaign_type") in MARKETING_TYPES else "warning", "message": "Marketing campaigns require unsubscribe link."},
        {"key": "purpose", "ok": bool(campaign.get("purpose")), "severity": "error", "message": "Campaign purpose is required."},
        {"key": "plain_text", "ok": bool(plain_body.strip()), "severity": "warning", "message": "Plain text fallback is recommended."},
        {"key": "deceptive_subject", "ok": not any(term in subject.lower() for term in ["re:", "fwd:", "free money", "urgent!!!"]), "severity": "warning", "message": "Subject should be truthful."},
        {"key": "recipient_limit", "ok": len(eligible) <= int(sender.get("daily_limit", 500) if sender else 0), "severity": "error", "message": "Eligible recipients exceed sender daily limit."},
    ]
    can_send = bool(eligible) and not any(not check["ok"] and check["severity"] == "error" for check in checks)
    return {"eligible": eligible, "eligible_count": len(eligible), "excluded_count": len(exclusions), "exclusions": exclusions, "checks": checks, "can_send": can_send}


def render_template(template: str, contact: dict, sender: dict, token: str) -> str:
    values = {
        "first_name": contact.get("first_name", ""),
        "last_name": contact.get("last_name", ""),
        "company": contact.get("company", ""),
        "sender_name": sender.get("sender_name", ""),
        "physical_address": sender.get("physical_address", ""),
        "unsubscribe_url": f"http://127.0.0.1:{PORT}/unsubscribe?token={token}",
    }
    result = template
    for key, value in values.items():
        result = result.replace("{{" + key + "}}", str(value))
    return result


def smtp_send(sender: dict, to_email: str, subject: str, html_body: str, plain_body: str) -> tuple[bool, str]:
    if sender.get("provider") == "dryrun":
        return True, "dry-run accepted; no real email sent"

    message = EmailMessage()
    message["From"] = f"{sender.get('sender_name', '')} <{sender.get('sender_email', '')}>"
    message["To"] = to_email
    message["Reply-To"] = sender.get("reply_to") or sender.get("sender_email")
    message["Subject"] = subject
    message.set_content(plain_body or "Please view this message in an HTML-capable client.")
    message.add_alternative(html_body, subtype="html")

    host = sender.get("host") or "127.0.0.1"
    port = int(sender.get("port") or 1025)
    username = sender.get("username") or ""
    password = SECRET_STORE.get(sender["id"]) or sender.get("password") or ""
    encryption = sender.get("encryption") or "none"
    try:
        if encryption == "ssl":
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=25) as client:
                if username:
                    client.login(username, password)
                client.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=25) as client:
                if encryption == "starttls":
                    client.starttls(context=ssl.create_default_context())
                if username:
                    client.login(username, password)
                client.send_message(message)
        return True, "sent"
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed. For Gmail use an app password with smtp.gmail.com:587 STARTTLS."
    except Exception as exc:
        return False, str(exc)


def test_sender_connection(sender: dict) -> tuple[bool, str]:
    if sender.get("provider") == "dryrun":
        return True, "Dry-run sender is ready. No real email will be sent."

    host = sender.get("host") or "127.0.0.1"
    port = int(sender.get("port") or 1025)
    username = sender.get("username") or ""
    password = SECRET_STORE.get(sender["id"]) or sender.get("password") or ""
    encryption = sender.get("encryption") or "none"

    if sender.get("provider") == "gmail" and not password:
        return False, "Gmail requires a Google app password. Save the sender again with the app password."
    if sender.get("provider") == "gmail" and (host != "smtp.gmail.com" or port != 587 or encryption != "starttls"):
        return False, "Recommended Gmail settings are smtp.gmail.com, port 587, STARTTLS."

    try:
        if encryption == "ssl":
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as client:
                if username:
                    client.login(username, password)
                client.noop()
        else:
            with smtplib.SMTP(host, port, timeout=20) as client:
                client.ehlo()
                if encryption == "starttls":
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if username:
                    client.login(username, password)
                client.noop()
        return True, "SMTP connection successful."
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed. For Gmail, use a Google app password, not your normal password."
    except Exception as exc:
        return False, f"SMTP connection failed: {exc}"


def send_campaign(state: dict, campaign: dict, test_email: str | None = None) -> dict:
    validation = validate_campaign(state, campaign)
    sender = next(item for item in state["senders"] if item["id"] == campaign["sender_id"])
    contacts = [{"id": "test", "email": test_email, "first_name": "Test", "last_name": "", "company": ""}] if test_email else validation["eligible"]
    if not test_email and not validation["can_send"]:
        return {"ok": False, "message": "Compliance validation failed", "validation": validation}
    if test_email and not valid_email(test_email):
        return {"ok": False, "message": "Invalid test email"}

    sent = failed = 0
    logs = []
    for contact in contacts:
        token = base64.urlsafe_b64encode(f"{campaign['id']}:{contact['email']}:{uuid.uuid4().hex}".encode()).decode().rstrip("=")
        html_body = render_template(campaign.get("html_body", ""), contact, sender, token)
        plain_body = render_template(campaign.get("plain_body", ""), contact, sender, token)
        ok, reason = smtp_send(sender, contact["email"], campaign["subject"], html_body, plain_body)
        event = {
            "id": uuid.uuid4().hex,
            "campaign_id": campaign["id"],
            "email": contact["email"],
            "event": "sent" if ok else "failed",
            "reason": reason,
            "created_at": now(),
            "test": bool(test_email),
        }
        state["events"].append(event)
        logs.append(event)
        sent += 1 if ok else 0
        failed += 0 if ok else 1
        if not test_email:
            campaign.setdefault("recipients", []).append({"email": contact["email"], "status": "sent" if ok else "failed", "reason": reason, "token": token})
        if not test_email:
            time.sleep(float(campaign.get("delay_seconds", 0.2)))
    if not test_email:
        campaign["status"] = "sent" if failed == 0 else "completed_with_failures"
        campaign["sent_at"] = now()
        campaign["audit"] = {
            "sent_at": campaign["sent_at"],
            "recipient_count": len(contacts),
            "sender_id": sender["id"],
            "compliance_checks": validation["checks"],
            "excluded": validation["exclusions"],
        }
    else:
        campaign["test_sent"] = sent > 0
        campaign["last_test_at"] = now()
        campaign["last_test_email"] = test_email
    return {"ok": True, "sent": sent, "failed": failed, "logs": logs}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, payload: dict | list, status: int = 200) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            with STATE_LOCK:
                state = load_state()
                clean = dict(state)
                clean["senders"] = [public_sender(sender) for sender in state["senders"]]
                self.send_json(clean)
            return
        if parsed.path == "/unsubscribe":
            query = parse_qs(parsed.query)
            token = query.get("token", [""])[0]
            with STATE_LOCK:
                state = load_state()
                email = ""
                for campaign in state["campaigns"]:
                    for recipient in campaign.get("recipients", []):
                        if recipient.get("token") == token:
                            email = recipient["email"]
                            recipient["status"] = "unsubscribed"
                if email and email not in [item["email"] for item in state["suppression"]]:
                    state["suppression"].append({"id": uuid.uuid4().hex, "email": email, "reason": "unsubscribed", "source": "one-click", "created_at": now()})
                save_state(state)
            body = b"<h1>You are unsubscribed</h1><p>This address has been suppressed from future marketing campaigns.</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/senders":
                payload = read_json(self)
                sender_id = payload.get("id") or uuid.uuid4().hex
                provider = payload.get("provider", "smtp")
                sender_email = normalize_email(payload.get("sender_email", ""))
                reply_to = normalize_email(payload.get("reply_to", "")) or sender_email
                if not valid_email(sender_email):
                    self.send_json({"ok": False, "message": "Valid From email is required."}, HTTPStatus.BAD_REQUEST)
                    return
                if reply_to and not valid_email(reply_to):
                    self.send_json({"ok": False, "message": "Valid Reply-To email is required."}, HTTPStatus.BAD_REQUEST)
                    return
                if provider == "gmail":
                    payload.setdefault("host", "smtp.gmail.com")
                    payload.setdefault("port", 587)
                    payload.setdefault("encryption", "starttls")
                    payload.setdefault("username", sender_email)
                sender = {
                    "id": sender_id,
                    "label": payload.get("label") or payload.get("sender_email") or "Sender",
                    "provider": provider,
                    "sender_name": payload.get("sender_name", ""),
                    "sender_email": sender_email,
                    "reply_to": reply_to,
                    "physical_address": payload.get("physical_address", ""),
                    "host": payload.get("host", "smtp.gmail.com" if provider == "gmail" else ""),
                    "port": int(payload.get("port") or (587 if provider == "gmail" else 25)),
                    "username": payload.get("username", ""),
                    "encryption": payload.get("encryption", "starttls" if provider == "gmail" else "none"),
                    "daily_limit": int(payload.get("daily_limit") or 500),
                    "hourly_limit": int(payload.get("hourly_limit") or 100),
                    "password_configured": False,
                    "created_at": now(),
                }
                if payload.get("password"):
                    SECRET_STORE[sender_id] = payload["password"]
                with STATE_LOCK:
                    state = load_state()
                    state["senders"] = [item for item in state["senders"] if item["id"] != sender_id]
                    state["senders"].append(sender)
                    save_state(state)
                self.send_json({"ok": True, "sender": public_sender(sender)})
                return

            if parsed.path == "/api/senders/test":
                payload = read_json(self)
                sender_id = payload.get("sender_id")
                with STATE_LOCK:
                    state = load_state()
                    sender = next((item for item in state["senders"] if item["id"] == sender_id), None)
                if not sender:
                    self.send_json({"ok": False, "message": "Sender not found"}, HTTPStatus.NOT_FOUND)
                    return
                ok, message = test_sender_connection(sender)
                self.send_json({"ok": ok, "message": message}, 200 if ok else 400)
                return

            if parsed.path == "/api/import":
                form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type")})
                upload = form["file"]
                filename = upload.filename or "upload.csv"
                blob = upload.file.read()
                rows = rows_from_xlsx(blob) if filename.lower().endswith(".xlsx") else rows_from_csv(blob)
                with STATE_LOCK:
                    state = load_state()
                    result = upsert_contacts(state, rows, "xlsx" if filename.lower().endswith(".xlsx") else "csv")
                    save_state(state)
                self.send_json({"ok": True, **result})
                return

            if parsed.path == "/api/campaigns":
                payload = read_json(self)
                campaign = {
                    "id": uuid.uuid4().hex,
                    "name": payload.get("name") or "Untitled campaign",
                    "campaign_type": payload.get("campaign_type", "newsletter"),
                    "sender_id": payload.get("sender_id"),
                    "subject": payload.get("subject", ""),
                    "purpose": payload.get("purpose", ""),
                    "html_body": payload.get("html_body", ""),
                    "plain_body": payload.get("plain_body", ""),
                    "delay_seconds": float(payload.get("delay_seconds", 0.2) or 0.2),
                    "status": "draft",
                    "created_at": now(),
                    "recipients": [],
                }
                with STATE_LOCK:
                    state = load_state()
                    state["campaigns"].append(campaign)
                    validation = validate_campaign(state, campaign)
                    save_state(state)
                self.send_json({"ok": True, "campaign": campaign, "validation": {k: v for k, v in validation.items() if k != "eligible"}})
                return

            match = re.match(r"^/api/campaigns/([^/]+)/(validate|send-test|send)$", parsed.path)
            if match:
                campaign_id, action = match.groups()
                payload = read_json(self)
                with STATE_LOCK:
                    state = load_state()
                    campaign = next((item for item in state["campaigns"] if item["id"] == campaign_id), None)
                    if not campaign:
                        self.send_json({"ok": False, "message": "Campaign not found"}, HTTPStatus.NOT_FOUND)
                        return
                    if action == "validate":
                        result = validate_campaign(state, campaign)
                        self.send_json({"ok": True, "validation": {k: v for k, v in result.items() if k != "eligible"}})
                        return
                    result = send_campaign(state, campaign, payload.get("test_email") if action == "send-test" else None)
                    save_state(state)
                    self.send_json(result, 200 if result.get("ok") else 400)
                    return

            self.send_json({"ok": False, "message": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"ok": False, "message": str(exc)}, HTTPStatus.BAD_REQUEST)


if __name__ == "__main__":
    DATA_DIR.mkdir(exist_ok=True)
    load_state()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"OmniAI Email Shooter running at http://127.0.0.1:{PORT}")
    server.serve_forever()
