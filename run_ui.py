from __future__ import annotations

import ast
import base64
import csv
import difflib
import html
import io
import json
import os
import re
import random
import smtplib
import ssl
import threading
import time
import uuid
import warnings
import zipfile
from datetime import datetime, timezone
from email.message import EmailMessage
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree
warnings.filterwarnings("ignore", message="'cgi' is deprecated.*", category=DeprecationWarning)
import cgi

try:
    import chat_llm  # type: ignore
except Exception:  # noqa: BLE001 — optional LLM backend
    chat_llm = None  # type: ignore


ROOT = Path(__file__).parent / "frontend" / "static"
DATA_DIR = Path(__file__).parent / "local_data"
STATE_FILE = DATA_DIR / "state.json"
CONFIG_FILE = DATA_DIR / "config.json"
PORT = int(os.environ.get("OMNIAI_UI_PORT", "5173"))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MARKETING_TYPES = {"marketing", "newsletter", "sales_outreach", "job_outreach", "follow_up"}
ALLOWED_CONSENT = {"opted_in", "soft_opt_in", "transactional"}
BLOCKED_CONSENT = {"unsubscribed", "bounced", "complained"}

# Phase 1: Fernet-encrypted on-disk credential store.
# SECRET_STORE remains a dict-compatible facade so existing call sites
# (``SECRET_STORE[id] = pw``, ``.get(id)``, ``.pop(id, None)``) need no change.
try:
    from secret_store import store as _secret_store_singleton  # type: ignore
except Exception:
    _secret_store_singleton = None


class _SecretStoreProxy:
    _mem: dict[str, str] = {}

    def __setitem__(self, k, v):
        if _secret_store_singleton is not None:
            _secret_store_singleton().set(k, v or "")
        else:
            self._mem[k] = v

    def get(self, k, default=None):
        if _secret_store_singleton is not None:
            val = _secret_store_singleton().get(k)
            return val if val is not None else default
        return self._mem.get(k, default)

    def pop(self, k, default=None):
        if _secret_store_singleton is not None:
            had = _secret_store_singleton().has(k)
            _secret_store_singleton().delete(k)
            return True if had else default
        return self._mem.pop(k, default)

    def __contains__(self, k):
        if _secret_store_singleton is not None:
            return _secret_store_singleton().has(k)
        return k in self._mem

    def __getitem__(self, k):
        if _secret_store_singleton is not None:
            val = _secret_store_singleton().get(k)
            if val is None:
                raise KeyError(k)
            return val
        return self._mem[k]

    def __len__(self):
        if _secret_store_singleton is not None:
            try:
                return _secret_store_singleton().count()
            except AttributeError:
                return 0
        return len(self._mem)

    def update(self, mapping):
        for k, v in (mapping or {}).items():
            self[k] = v

    def items(self):
        """Yield (key, value) for every stored secret; used by persist_secrets()."""
        if _secret_store_singleton is not None:
            try:
                yield from _secret_store_singleton().items()
                return
            except AttributeError:
                pass
        yield from self._mem.items()


SECRET_STORE = _SecretStoreProxy()
STATE_LOCK = threading.Lock()
CHAT_SESSIONS: dict[str, dict] = {}
CHAT_LOCK = threading.Lock()
LAST_UPLOAD_INVALID: list[dict] = []   # rows rejected by the most recent /api/import call
LAST_UPLOAD_NAME: str = ""

# ---------- Encryption at rest (production hardening) ----------
# SMTP passwords / app passwords are stored encrypted in local_data/secrets.enc using
# Fernet (AES-128-CBC + HMAC). The Fernet key comes from OMNIAI_SECRET_KEY if set,
# otherwise auto-generated to local_data/.secret_key on first run. Never committed,
# never logged. Restart-safe — passwords survive across reboots without being re-typed.

SECRETS_FILE = None  # type: Path | None  (set lazily once DATA_DIR exists)
KEY_FILE = None  # type: Path | None
_FERNET = None  # type: Fernet | None


def _fernet():
    """Lazy-init the Fernet cipher; reuse for the process lifetime."""
    global _FERNET, SECRETS_FILE, KEY_FILE
    if _FERNET is not None:
        return _FERNET
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None  # encryption disabled silently — caller falls back to in-memory store
    DATA_DIR.mkdir(exist_ok=True)
    KEY_FILE = DATA_DIR / ".secret_key"
    SECRETS_FILE = DATA_DIR / "secrets.enc"
    key = os.environ.get("OMNIAI_SECRET_KEY", "").encode() or None
    if not key:
        if KEY_FILE.exists():
            key = KEY_FILE.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            KEY_FILE.write_bytes(key)
            try:
                os.chmod(KEY_FILE, 0o600)  # no-op on Windows but useful elsewhere
            except Exception:
                pass
    _FERNET = Fernet(key)
    return _FERNET


def encrypt_secret(plain: str) -> str:
    """Return the Fernet-encrypted token for storage. Empty input returns ''."""
    if not plain:
        return ""
    f = _fernet()
    if not f:
        return plain  # cryptography missing — caller still gets a value, but unencrypted
    return f.encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    """Reverse of encrypt_secret. Returns '' on failure or empty input."""
    if not token:
        return ""
    f = _fernet()
    if not f:
        return token
    try:
        return f.decrypt(token.encode()).decode()
    except Exception:
        return ""


def persist_secrets() -> None:
    """No-op kept for backward compatibility.

    The encrypted credential store is now `secret_store.py` (see
    `local_data/secrets.bin`). It writes through atomically on every
    SECRET_STORE.set() via the proxy in this module, so explicit persistence
    calls are unnecessary. Kept as a no-op to avoid breaking existing call
    sites that the linter and earlier code already invoke.
    """
    return


def load_persisted_secrets() -> None:
    """No-op: secret_store.py's singleton loads on first .get/.set automatically."""
    if _secret_store_singleton is not None:
        try:
            _secret_store_singleton()  # trigger lazy init so secrets.bin is read
        except Exception:
            pass


def crypto_status() -> dict:
    """For the diagnostic endpoint."""
    try:
        import cryptography  # noqa: F401
        sdk = True
    except ImportError:
        sdk = False
    stored = 0
    secrets_path = None
    if _secret_store_singleton is not None:
        try:
            s = _secret_store_singleton()
            stored = s.count()
            secrets_path = str(s._path)
        except Exception:
            stored = -1
    return {
        "encryption_sdk": sdk,
        "encryption_enabled": _secret_store_singleton is not None,
        "secrets_file": secrets_path,
        "stored_secrets": stored,
    }


# ---------- Audit log (user-action trail) ----------

def audit(state: dict, action: str, entity_type: str = "", entity_id: str = "",
          details: dict | None = None, user_id: str = "local") -> None:
    """Append a row to state.audit_log. State must be saved by the caller."""
    state.setdefault("audit_log", []).append({
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action": action,
        "details": details or {},
        "created_at": now(),
    })


# ---------- Spam-score heuristic ----------
# Cheap, rules-based — flags subject/body content that frequently trips spam filters.
# This is NOT a guarantee. Result: {score: 0..100, flags: [str]}.

_SUSPICIOUS_WORDS = {
    "free", "viagra", "cialis", "winner", "won", "congratulations", "urgent",
    "act now", "limited time", "click here", "guaranteed", "risk-free",
    "earn money", "100% free", "make money", "extra cash", "cheap", "no cost",
    "buy now", "order now", "get rich", "investment opportunity",
}


def score_spam(subject: str, html_body: str = "", plain_body: str = "") -> dict:
    """Return a coarse spam score 0..100 plus the flags that contributed."""
    subject = subject or ""
    body = (plain_body or html_body or "")
    text = (subject + " " + body).lower()
    score = 0
    flags = []

    # Caps ratio in subject (loud subject lines trip filters)
    if subject:
        letters = [c for c in subject if c.isalpha()]
        if letters:
            caps_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
            if caps_ratio > 0.5 and len(letters) > 6:
                score += 25
                flags.append(f"subject is {int(caps_ratio*100)}% uppercase")

    # Exclamation marks
    excl = subject.count("!") + body.count("!")
    if excl >= 3:
        score += min(20, 5 * excl)
        flags.append(f"{excl} exclamation marks")

    # Suspicious phrases
    hits = sorted({w for w in _SUSPICIOUS_WORDS if w in text})
    if hits:
        score += min(30, 8 * len(hits))
        flags.append("suspicious phrases: " + ", ".join(hits[:5]))

    # Link density (link count vs words)
    link_count = len(re.findall(r"https?://", body, re.IGNORECASE)) + body.count("href=")
    word_count = max(len(re.findall(r"\w+", body)), 1)
    if link_count >= 5 and link_count / word_count > 0.05:
        score += 15
        flags.append(f"{link_count} links — high link density")

    # No unsubscribe text (only flag if marketing-style — caller decides severity)
    has_unsub = "unsubscribe" in body.lower() or "{{unsubscribe_url}}" in body
    if not has_unsub:
        score += 10
        flags.append("no unsubscribe text in body")

    # Currency / urgency
    if re.search(r"\$\$+|\$[0-9]+,?[0-9]{3,}|FREE|URGENT|ACT NOW", subject + " " + body):
        score += 10
        flags.append("loud currency / urgency wording")

    score = min(100, score)
    return {"score": score, "flags": flags}


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


def _migrate_state(state: dict) -> bool:
    """Phase 1 schema migration. Returns True if state was changed.

    - Ensures schema_version field exists.
    - Creates a default ContactList and migrates the legacy global ``contacts``
      array into it. Idempotent.
    - Ensures ``contact_lists``, ``audit_log``, ``email_jobs`` arrays exist.
    """
    changed = False
    if state.get("schema_version", 1) < 2:
        state["schema_version"] = 2
        changed = True
    for key in ("contact_lists", "audit_log", "email_jobs", "email_templates"):
        if key not in state:
            state[key] = []
            changed = True
    # Migrate legacy contacts (no contact_list_id) into a Default list.
    default_id = "default-list"
    if state.get("contacts") and not any(c.get("contact_list_id") for c in state["contacts"]):
        if not any(cl["id"] == default_id for cl in state["contact_lists"]):
            state["contact_lists"].append({
                "id": default_id,
                "user_id": "local",
                "name": "Default",
                "source_type": "migrated",
                "total_rows": len(state["contacts"]),
                "valid_count": sum(1 for c in state["contacts"] if c.get("email")),
                "invalid_count": 0,
                "duplicate_count": 0,
                "created_at": now(),
                "updated_at": now(),
            })
            changed = True
        for c in state["contacts"]:
            if not c.get("contact_list_id"):
                c["contact_list_id"] = default_id
                c.setdefault("is_valid", True)
                c.setdefault("unsubscribed", False)
                changed = True
    return changed


def load_state() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if not STATE_FILE.exists():
        state = seed_state()
        _migrate_state(state)
        save_state(state)
        return state
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        state = seed_state()
        _migrate_state(state)
        save_state(state)
        return state
    dirty = False
    sender_ids = {sender.get("id") for sender in state.get("senders", [])}
    if "local-dryrun" not in sender_ids:
        state["senders"].append(seed_state()["senders"][1])
        dirty = True
    if _migrate_state(state):
        dirty = True
    if dirty:
        save_state(state)
    return state


def read_config() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def write_config(cfg: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    tmp_file = STATE_FILE.with_suffix(".json.tmp")
    tmp_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp_file.replace(STATE_FILE)


def public_sender(sender: dict) -> dict:
    clean = dict(sender)
    clean.pop("password", None)
    clean["password_configured"] = bool(SECRET_STORE.get(sender["id"]))
    return clean


# ---------- Phase 1: audit log ----------
def audit(state: dict, action: str, entity_type: str = "", entity_id: str = "",
          details: dict | None = None, user_id: str = "local") -> None:
    """Append an entry to the audit log. Truncates to last 2000 entries."""
    state.setdefault("audit_log", []).append({
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "details": details or {},
        "created_at": now(),
    })
    # Cap log length to prevent unbounded growth.
    if len(state["audit_log"]) > 2000:
        state["audit_log"] = state["audit_log"][-2000:]


def public_contact_list(cl: dict) -> dict:
    return {k: v for k, v in cl.items() if not k.startswith("_")}


def public_email_provider(provider: dict) -> dict:
    """REST-shape view of a provider. Aliases legacy sender fields to the spec
    names (provider_type, provider_name, smtp_username, encryption_type, ...)
    while leaving the underlying state untouched."""
    out = {
        "id": provider["id"],
        "user_id": provider.get("user_id", "local"),
        "provider_type": "gmail" if provider.get("provider") == "gmail" else ("smtp" if provider.get("provider") == "smtp" else provider.get("provider")),
        "provider_name": provider.get("label"),
        "sender_name": provider.get("sender_name", ""),
        "sender_email": provider.get("sender_email", ""),
        "smtp_host": provider.get("host", ""),
        "smtp_port": provider.get("port", 0),
        "smtp_username": provider.get("username", ""),
        "encryption_type": provider.get("encryption", "none"),
        "reply_to_email": provider.get("reply_to", ""),
        "daily_limit": int(provider.get("daily_limit", 500)),
        "per_minute_limit": int(provider.get("hourly_limit", 100) or 100) // 60 or 1,
        "is_active": provider.get("is_active", True),
        "last_test_status": provider.get("last_test_status", ""),
        "last_test_message": provider.get("last_test_message", ""),
        "last_tested_at": provider.get("last_tested_at"),
        "created_at": provider.get("created_at"),
        "updated_at": provider.get("updated_at", provider.get("created_at")),
        "password_configured": bool(SECRET_STORE.get(provider["id"])),
    }
    return out


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
    # Spam-score heuristic — flagged as a warning so it doesn't block dry-runs,
    # but the score + flags are surfaced so the user can fix the content.
    spam = score_spam(subject, html_body, plain_body)
    if spam["score"] >= 40:
        checks.append({
            "key": "spam_score",
            "ok": False,
            "severity": "warning",
            "message": f"High spam score ({spam['score']}/100): " + "; ".join(spam["flags"][:3]),
        })
    can_send = bool(eligible) and not any(not check["ok"] and check["severity"] == "error" for check in checks)
    return {"eligible": eligible, "eligible_count": len(eligible), "excluded_count": len(exclusions), "exclusions": exclusions, "checks": checks, "can_send": can_send, "spam": spam}


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
                client.ehlo()
                if encryption == "starttls":
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if username:
                    client.login(username, password)
                client.send_message(message)
        return True, "sent"
    except smtplib.SMTPResponseException as resp:
        # Propagate SMTP response exceptions with code for the caller to decide
        return False, f"SMTP error {resp.smtp_code}: {resp.smtp_error}"
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed. For Gmail, use a Google app password, not your normal password."
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
    # Validate and prepare
    validation = validate_campaign(state, campaign)
    sender = next(item for item in state["senders"] if item["id"] == campaign["sender_id"])
    if test_email and not valid_email(test_email):
        return {"ok": False, "message": "Invalid test email"}
    if not test_email and not validation["can_send"]:
        return {"ok": False, "message": "Compliance validation failed", "validation": validation}

    recipients = [{"id": "test", "email": test_email, "first_name": "Test", "last_name": "", "company": ""}] if test_email else validation["eligible"]

    # For test sends, perform synchronous small send
    if test_email:
        sent = failed = 0
        logs = []
        for contact in recipients:
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
                "test": True,
            }
            state["events"].append(event)
            logs.append(event)
            sent += 1 if ok else 0
            failed += 0 if ok else 1
        campaign["test_sent"] = sent > 0
        campaign["last_test_at"] = now()
        campaign["last_test_email"] = test_email
        return {"ok": True, "sent": sent, "failed": failed, "logs": logs}

    # For bulk sends, queue and return quickly; run the sender in a background thread
    campaign.setdefault("recipients", [])
    # build recipients list saved in campaign if not already present
    if not campaign["recipients"]:
        campaign["recipients"] = [{"email": c["email"], "status": "queued", "token": None} for c in recipients]
    campaign["status"] = "queued"
    campaign["queued_at"] = now()
    save_state(state)

    def send_loop():
        MAX_DAILY = int(sender.get("daily_limit") or 500)
        base_delay = float(campaign.get("delay_seconds") or 0.2)
        jitter_min = max(0.0, base_delay * 0.75)
        jitter_max = max(jitter_min + 0.05, base_delay * 1.25)
        cooldown_every = 50 if base_delay >= 30 else 0
        cooldown_seconds = 15 * 60

        sent = 0
        failed = 0
        rng = __import__('random')

        # resume index
        index = 0
        for i, r in enumerate(campaign.get("recipients", [])):
            if r.get("status") in ("sent", "failed", "unsubscribed"):
                index += 1
                continue
            # enforce daily cap
            if sent >= MAX_DAILY or index >= MAX_DAILY:
                campaign["status"] = "paused-daily-cap"
                campaign.setdefault("audit", {})["sent_at"] = now()
                save_state(state)
                return

            recipient_email = r.get("email")
            token = base64.urlsafe_b64encode(f"{campaign['id']}:{recipient_email}:{uuid.uuid4().hex}".encode()).decode().rstrip("=")
            # simple contact placeholder for templating
            contact = {"email": recipient_email, "first_name": "", "last_name": "", "company": ""}
            html_body = render_template(campaign.get("html_body", ""), contact, sender, token)
            plain_body = render_template(campaign.get("plain_body", ""), contact, sender, token)

            ok, reason = smtp_send(sender, recipient_email, campaign["subject"], html_body, plain_body)
            event = {"id": uuid.uuid4().hex, "campaign_id": campaign["id"], "email": recipient_email, "event": "sent" if ok else "failed", "reason": reason, "created_at": now(), "test": False}
            with STATE_LOCK:
                state["events"].append(event)
                if ok:
                    r["status"] = "sent"
                    r["token"] = token
                    sent += 1
                else:
                    r["status"] = "failed"
                    r.setdefault("reason", reason)
                    failed += 1
                # persist progress
                campaign["last_progress_index"] = index
                campaign["sent_count"] = campaign.get("sent_count", 0) + (1 if ok else 0)
                save_state(state)

            # error handling for rate limiting
            try:
                # If smtp_send returned a message that includes SMTP response code, detect 421/535
                if isinstance(reason, str) and ("SMTP error 421" in reason or "SMTP error 535" in reason or "421" in reason or "535" in reason):
                    with STATE_LOCK:
                        campaign["status"] = "paused-rate-limit"
                        campaign["paused_reason"] = reason
                        save_state(state)
                    return
            except Exception:
                pass

            # jitter between sends
            sleep_for = rng.uniform(jitter_min, jitter_max)
            time.sleep(sleep_for)

            index += 1
            # cooldown every N emails (only when base_delay is set high for production)
            if cooldown_every and sent > 0 and sent % cooldown_every == 0:
                time.sleep(cooldown_seconds)

        with STATE_LOCK:
            campaign["status"] = "sent" if failed == 0 else "completed_with_failures"
            campaign["sent_at"] = now()
            campaign.setdefault("audit", {})["recipient_count"] = len(campaign.get("recipients", []))
            save_state(state)

    thread = threading.Thread(target=send_loop, daemon=True)
    thread.start()
    return {"ok": True, "message": "Campaign queued and sending in background"}


# ---------- Chatbot ----------

CHAT_HELP = """Hi — I can drive every action in this app via chat.

**Configure sender**
- `gmail you@gmail.com abcd efgh ijkl mnop` &mdash; save Gmail + verify
- `configure gmail` &mdash; guided two-step (asks email, then app password)
- `dryrun` &mdash; use the safe dry-run sender (no real email)
- `smtp <host> <port> <user> <pass> from <email>` &mdash; custom SMTP
- `use sender <label or email>` · `test connection` · `delete sender <ref>`

**Recipients**
- 📎 paperclip to attach a CSV/XLSX
- `add alice@x.com bob@y.com carol@z.com` &mdash; add by paste
- `list recipients` · `count recipients`
- `list opted_in` · `list bounced` · `list unknown` &nbsp;(also: soft_opt_in, transactional, unsubscribed, complained)
- `breakdown` &mdash; consent counts table
- `find alice@x.com` · `find @gmail.com` &mdash; lookup by email or domain
- `remove alice@x.com` · `clear recipients`
- `suppress alice@x.com reason bounced` · `unsuppress alice@x.com`

**Campaign draft**
- `new campaign Spring update`
- `subject Hello {{first_name}}` · `set subject to ...`
- `purpose Monthly update.` · `type marketing`
- `html <h2>Hi</h2>...` · `plain ...` · `delay 1.5`
- `template newsletter` &nbsp;(or: `sales`, `minimal`, `transactional`)
- `show draft` · `clear draft` · `save campaign`
- `delete campaign <name or id>`

**Send**
- `test me@inbox.com` &mdash; send a test
- `send bulk` &mdash; fire the bulk send (uses saved campaign + all eligible recipients)
- `progress` &mdash; live progress

**One-shot send to specific people**
- `send to alice@x.com, bob@y.com: Hi everyone, quick update...` &mdash; auto-adds + sends
- `email a@x.com, b@y.com subject "Hello" body "Hi {{first_name}}, ..."`
- Paste a whole email block (headers + body) — I parse `Subject:`, `To:`, body:
  ```
  To: alice@x.com, bob@y.com
  Subject: Quick update

  Hi everyone, here's what's new...
  ```

**Inspect**
- `status` · `list senders` · `list campaigns` · `events` · `suppression`
- `help <topic>` &mdash; topical help (gmail · compose · send · recipients)

Variables in subject/body: `{{first_name}} {{last_name}} {{company}} {{sender_name}} {{physical_address}} {{unsubscribe_url}}`.

Aliases work too — try `who`, `what's loaded`, `set up gmail`, `send to all`, `nuke contacts`, etc."""

CAMPAIGN_TYPES = ["newsletter","marketing","transactional","sales_outreach","job_outreach","follow_up"]


def chat_session(sid: str) -> dict:
    with CHAT_LOCK:
        if sid not in CHAT_SESSIONS:
            CHAT_SESSIONS[sid] = {
                "draft": {},
                "pending": None,
                "last_campaign_id": None,
                "last_sender_id": None,
                "active_sender_id": None,
            }
        return CHAT_SESSIONS[sid]


def _find_sender(state: dict, ref: str) -> dict | None:
    ref = (ref or "").strip().lower()
    if not ref:
        return None
    for s in state.get("senders", []):
        if s["id"] == ref or s.get("label","").lower() == ref or s.get("sender_email","").lower() == ref:
            return s
    return None


def chat_save_gmail(state: dict, sess: dict, email: str, password: str, name: str = "") -> dict:
    email = normalize_email(email)
    password = (password or "").strip()
    if not valid_email(email):
        return {"reply": f"⚠️ `{email}` doesn't look like a valid email."}
    if not password:
        return {"reply": "⚠️ Missing app password."}
    # find or create sender
    existing = next((s for s in state.get("senders", []) if s.get("provider")=="gmail" and s.get("sender_email")==email), None)
    sender_id = existing["id"] if existing else uuid.uuid4().hex
    label = (existing["label"] if existing else "Gmail — " + email)
    sender = {
        "id": sender_id, "label": label, "provider": "gmail",
        "sender_name": name or (existing.get("sender_name") if existing else "OmniAI Sender"),
        "sender_email": email, "reply_to": email,
        "physical_address": existing.get("physical_address","") if existing else "",
        "host": "smtp.gmail.com", "port": 587, "username": email, "encryption": "starttls",
        "daily_limit": existing.get("daily_limit", 500) if existing else 500,
        "hourly_limit": existing.get("hourly_limit", 100) if existing else 100,
        "password_configured": False, "created_at": existing.get("created_at", now()) if existing else now(),
    }
    SECRET_STORE[sender_id] = password
    persist_secrets()
    state["senders"] = [s for s in state.get("senders", []) if s["id"] != sender_id]
    state["senders"].append(sender)
    sess["active_sender_id"] = sender_id
    sess["last_sender_id"] = sender_id
    # verify connection
    ok, msg = test_sender_connection(sender)
    audit(state, action="sender.gmail.saved" + (".verified" if ok else ".verify_failed"),
          entity_type="sender", entity_id=sender_id,
          details={"email": email, "verified": ok, "via": "chat"})
    save_state(state)
    if ok:
        return {"reply": f"✅ Saved Gmail sender `{email}` and verified SMTP connection. {msg}", "state_dirty": True}
    else:
        return {"reply": f"⚠️ Saved Gmail sender `{email}` but SMTP test failed: {msg}", "state_dirty": True}


def chat_save_dryrun(state: dict, sess: dict) -> dict:
    sender = next((s for s in state.get("senders", []) if s.get("provider")=="dryrun"), None)
    if not sender:
        sender_id = uuid.uuid4().hex
        sender = {
            "id": sender_id, "label": "Safe Dry Run", "provider": "dryrun",
            "sender_name": "OmniAI Sender", "sender_email": "dryrun@omniai.local",
            "reply_to": "dryrun@omniai.local",
            "physical_address": "123 Compliance Street, Pune, India",
            "host": "dryrun.local", "port": 0, "username": "", "encryption": "none",
            "daily_limit": 500, "hourly_limit": 100,
            "password_configured": False, "created_at": now(),
        }
        state["senders"].append(sender)
        save_state(state)
    sess["active_sender_id"] = sender["id"]
    return {"reply": f"✅ Using **{sender['label']}** as the active sender. No real email will be delivered.", "state_dirty": True}


def chat_save_smtp(state: dict, sess: dict, host: str, port: int, username: str, password: str, from_email: str, encryption: str = "starttls") -> dict:
    from_email = normalize_email(from_email or username)
    if not valid_email(from_email):
        return {"reply": "⚠️ Need a valid From email. Try `smtp <host> <port> <user> <password> from you@x.com`."}
    sender_id = uuid.uuid4().hex
    sender = {
        "id": sender_id, "label": f"SMTP {host}", "provider": "smtp",
        "sender_name": "OmniAI Sender", "sender_email": from_email, "reply_to": from_email,
        "physical_address": "",
        "host": host, "port": int(port), "username": username, "encryption": encryption,
        "daily_limit": 500, "hourly_limit": 100,
        "password_configured": False, "created_at": now(),
    }
    SECRET_STORE[sender_id] = password
    persist_secrets()
    state["senders"].append(sender)
    save_state(state)
    sess["active_sender_id"] = sender_id
    ok, msg = test_sender_connection(sender)
    return {"reply": f"{'✅' if ok else '⚠️'} SMTP saved. {msg}", "state_dirty": True}


def chat_set_draft(sess: dict, key: str, value: str) -> dict:
    sess["draft"][key] = value
    show = value if len(value) < 80 else value[:77] + "…"
    nice = {"name":"campaign name","campaign_type":"campaign type","subject":"subject","purpose":"purpose","html_body":"HTML body","plain_body":"plain text body","delay_seconds":"per-recipient delay"}.get(key, key)
    return {"reply": f"📝 Set **{nice}** → `{show}`.\nType `show draft` to see all fields or `save campaign` when ready."}


def chat_show_draft(sess: dict, state: dict) -> dict:
    d = sess.get("draft", {})
    if not d and not sess.get("active_sender_id"):
        return {"reply": "No draft yet. Start with `new campaign <name>`."}
    sender = next((s for s in state.get("senders", []) if s["id"]==sess.get("active_sender_id")), None)
    fields = [
        ("Name", d.get("name", "(unset)")),
        ("Type", d.get("campaign_type", "newsletter")),
        ("Subject", d.get("subject", "(unset)")),
        ("Purpose", d.get("purpose", "(unset)")),
        ("Delay", str(d.get("delay_seconds", 0.2)) + "s"),
        ("Sender", sender["label"] + " — " + sender["sender_email"] if sender else "(none — set one with `gmail …` or `dryrun`)"),
        ("HTML body", (d.get("html_body") or "(unset)")[:120] + ("…" if len(d.get("html_body","")) > 120 else "")),
        ("Plain body", (d.get("plain_body") or "(unset)")[:120] + ("…" if len(d.get("plain_body","")) > 120 else "")),
    ]
    rows = [[k, v] for k, v in fields]
    return {"reply": "📋 **Current draft**", "rich": {"type":"table","headers":["field","value"],"rows":rows}}


def chat_save_campaign(state: dict, sess: dict) -> dict:
    d = sess.get("draft", {})
    sender_id = sess.get("active_sender_id")
    if not sender_id:
        return {"reply": "⚠️ No sender set. Use `gmail you@gmail.com APPPASSWORD` or `dryrun`."}
    if not d.get("subject") or not d.get("html_body") or not d.get("purpose"):
        missing = [k for k in ("subject","html_body","purpose") if not d.get(k)]
        return {"reply": f"⚠️ Missing fields: {', '.join(missing)}. Set them with `subject ...`, `html ...`, `purpose ...`."}
    campaign = {
        "id": uuid.uuid4().hex,
        "name": d.get("name", "Chat campaign"),
        "campaign_type": d.get("campaign_type", "newsletter"),
        "sender_id": sender_id,
        "subject": d.get("subject", ""),
        "purpose": d.get("purpose", ""),
        "html_body": d.get("html_body", ""),
        "plain_body": d.get("plain_body", ""),
        "delay_seconds": float(d.get("delay_seconds", 0.2)),
        "status": "draft",
        "created_at": now(),
        "recipients": [],
    }
    state["campaigns"].append(campaign)
    validation = validate_campaign(state, campaign)
    audit(state, action="campaign.saved", entity_type="campaign", entity_id=campaign["id"],
          details={"name": campaign["name"], "eligible_count": validation.get("eligible_count", 0),
                   "can_send": validation.get("can_send", False),
                   "spam_score": (validation.get("spam") or {}).get("score", 0)})
    save_state(state)
    sess["last_campaign_id"] = campaign["id"]
    summary = f"💾 Saved campaign **{campaign['name']}** (`{campaign['id'][:8]}…`).\n"
    summary += f"- **{validation['eligible_count']}** eligible · {validation['excluded_count']} excluded\n"
    failed = [c for c in validation["checks"] if not c["ok"]]
    if failed:
        summary += "- Failing checks:\n"
        for c in failed:
            mark = "❌" if c["severity"]=="error" else "⚠️"
            summary += f"   {mark} `{c['key']}`: {c['message']}\n"
    if validation["can_send"]:
        summary += "\n✅ **Ready to send.** Try `test you@inbox.com` then `send bulk`."
    else:
        summary += "\n🔧 Fix the red checks, then `save campaign` again."
    return {"reply": summary, "state_dirty": True}


def chat_test_send(state: dict, sess: dict, to_email: str) -> dict:
    cid = sess.get("last_campaign_id")
    if not cid:
        return {"reply": "⚠️ Save a campaign first with `save campaign`."}
    campaign = next((c for c in state["campaigns"] if c["id"]==cid), None)
    if not campaign:
        return {"reply": "⚠️ Campaign not found in state."}
    result = send_campaign(state, campaign, test_email=to_email)
    save_state(state)
    if result.get("ok"):
        return {"reply": f"📨 Test sent: **{result.get('sent',0)} ok**, {result.get('failed',0)} failed → `{to_email}`.", "state_dirty": True}
    return {"reply": f"⚠️ Test failed: {result.get('message','unknown')}", "state_dirty": True}


def chat_bulk_send(state: dict, sess: dict) -> dict:
    cid = sess.get("last_campaign_id")
    if not cid:
        return {"reply": "⚠️ No saved campaign. Run `save campaign` first."}
    campaign = next((c for c in state["campaigns"] if c["id"]==cid), None)
    if not campaign:
        return {"reply": "⚠️ Campaign not found."}
    validation = validate_campaign(state, campaign)
    if not validation["can_send"]:
        failed = [c for c in validation["checks"] if not c["ok"] and c["severity"]=="error"]
        return {"reply": "⚠️ Cannot send — compliance checks failing:\n" + "\n".join(f"- ❌ `{c['key']}`: {c['message']}" for c in failed)}
    result = send_campaign(state, campaign, test_email=None)
    audit(state, action="campaign.bulk_send_launched", entity_type="campaign", entity_id=campaign["id"],
          details={"name": campaign["name"], "eligible_count": validation.get("eligible_count", 0),
                   "sender_id": campaign.get("sender_id"), "ok": bool(result.get("ok"))})
    save_state(state)
    if result.get("ok"):
        return {"reply": f"🚀 Bulk send queued for **{validation['eligible_count']}** recipients. Type `progress` to check.", "state_dirty": True}
    return {"reply": f"⚠️ Send failed: {result.get('message','unknown')}", "state_dirty": True}


def chat_progress(state: dict, sess: dict) -> dict:
    cid = sess.get("last_campaign_id")
    if not cid:
        return {"reply": "No campaign to check yet."}
    campaign = next((c for c in state["campaigns"] if c["id"]==cid), None)
    if not campaign:
        return {"reply": "Campaign not found."}
    recip = campaign.get("recipients", [])
    sent = sum(1 for r in recip if r.get("status")=="sent")
    failed = sum(1 for r in recip if r.get("status")=="failed")
    queued = sum(1 for r in recip if r.get("status")=="queued")
    pct = round((sent+failed)/len(recip)*100) if recip else 0
    return {"reply": f"📊 **{campaign['name']}** — status `{campaign['status']}` · {sent} sent · {failed} failed · {queued} queued · **{pct}%**"}


def chat_status(state: dict, sess: dict) -> dict:
    contacts = len(state.get("contacts", []))
    senders = state.get("senders", [])
    ready = sum(1 for s in senders if s.get("password_configured") or s.get("provider")=="dryrun")
    campaigns = state.get("campaigns", [])
    last = campaigns[-1] if campaigns else None
    active = next((s for s in senders if s["id"]==sess.get("active_sender_id")), None)
    lines = [
        f"📊 **Status**",
        f"- Active sender: " + (f"**{active['label']}** ({active['sender_email']})" if active else "_none_ — set with `gmail …` or `dryrun`"),
        f"- Recipients: **{contacts}**",
        f"- Senders ready: **{ready}** of {len(senders)}",
        f"- Campaigns: **{len(campaigns)}**",
    ]
    if last:
        recip = last.get("recipients", [])
        sent = sum(1 for r in recip if r.get("status")=="sent")
        lines.append(f"- Last: **{last['name']}** · `{last['status']}` · {sent}/{len(recip)} sent")
    return {"reply": "\n".join(lines)}


CONSENT_VALUES = ["opted_in","soft_opt_in","transactional","unknown","unsubscribed","bounced","complained"]

CHAT_TEMPLATES = {
    "newsletter": {
        "name": "Newsletter template applied",
        "subject": "{{first_name}}, what's new from {{sender_name}}",
        "purpose": "Monthly newsletter update to opted-in contacts.",
        "campaign_type": "newsletter",
        "html": (
            "<h2>Hi {{first_name}},</h2>\n"
            "<p>Here's the latest from {{sender_name}} — three quick highlights for you.</p>\n"
            "<ul><li>Highlight one</li><li>Highlight two</li><li>Highlight three</li></ul>\n"
            "<p>Thanks for reading.</p>\n"
            "<p><a href=\"{{unsubscribe_url}}\">Unsubscribe</a> · {{physical_address}}</p>"
        ),
        "plain": (
            "Hi {{first_name}},\n\n"
            "Here's the latest from {{sender_name}}:\n- Highlight one\n- Highlight two\n- Highlight three\n\n"
            "Unsubscribe: {{unsubscribe_url}}\n{{physical_address}}"
        ),
    },
    "sales": {
        "name": "Sales outreach template applied",
        "subject": "Quick thought for {{first_name}} at {{company}}",
        "purpose": "Sales outreach to consented prospects.",
        "campaign_type": "sales_outreach",
        "html": (
            "<p>Hi {{first_name}},</p>\n"
            "<p>I'm reaching out from {{sender_name}} because I think {{company}} might benefit from what we're building.</p>\n"
            "<p>Would a 15-minute chat make sense? If not, reply STOP and you won't hear from me again.</p>\n"
            "<p>— {{sender_name}}<br><a href=\"{{unsubscribe_url}}\">Unsubscribe</a> · {{physical_address}}</p>"
        ),
        "plain": (
            "Hi {{first_name}},\n\n"
            "I'm reaching out from {{sender_name}} because I think {{company}} might benefit from what we're building.\n"
            "Would a 15-minute chat make sense? If not, reply STOP and you won't hear from me again.\n\n"
            "— {{sender_name}}\nUnsubscribe: {{unsubscribe_url}}\n{{physical_address}}"
        ),
    },
    "minimal": {
        "name": "Minimal plain template applied",
        "subject": "A quick note from {{sender_name}}",
        "purpose": "Generic update to consented contacts.",
        "campaign_type": "newsletter",
        "html": (
            "<p>Hi {{first_name}},</p>\n<p>(write your message here)</p>\n"
            "<p><a href=\"{{unsubscribe_url}}\">Unsubscribe</a> · {{physical_address}}</p>"
        ),
        "plain": "Hi {{first_name}},\n\n(write your message here)\n\nUnsubscribe: {{unsubscribe_url}}\n{{physical_address}}",
    },
    "transactional": {
        "name": "Transactional template applied",
        "subject": "Update on your account, {{first_name}}",
        "purpose": "Transactional notice (not marketing).",
        "campaign_type": "transactional",
        "html": (
            "<p>Hi {{first_name}},</p>\n"
            "<p>This is a transactional notice regarding your account at {{company}}.</p>\n"
            "<p>(details here)</p>\n"
            "<p>— {{sender_name}}<br>{{physical_address}}</p>"
        ),
        "plain": (
            "Hi {{first_name}},\n\nThis is a transactional notice regarding your account at {{company}}.\n(details here)\n\n— {{sender_name}}\n{{physical_address}}"
        ),
    },
}

CHAT_KNOWN = [
    "help","status","dryrun","configure gmail","list senders","list recipients","list campaigns",
    "list opted_in","list bounced","list unknown","breakdown","find","add","remove","suppress","unsuppress",
    "new campaign","subject","purpose","type","html","plain","delay","template","show draft","clear draft",
    "save campaign","test","send bulk","progress","events","suppression","clear recipients","delete sender",
    "delete campaign","use sender","test connection",
    # ---- enhanced AI vocabulary ----
    "quickstart","setup","next","what next","failures","errors","draft about",
    "template newsletter","template marketing","template follow_up","template thank_you","template announce",
]

CHAT_ACKS = ["✓ Done.", "Got it.", "Sure thing.", "Noted.", "OK."]


def _ack() -> str:
    return random.choice(CHAT_ACKS)


def _norm_phrasing(low: str) -> str:
    """Normalize common natural-language variants to canonical forms."""
    rewrites = [
        (r"\bwho('?s| is)\b(?:.*?\b)(loaded|on the list|in the pool)", "list recipients"),
        (r"^what'?s loaded\??$", "status"),
        (r"^what can (?:you|i) do\??$", "help"),
        (r"^where am i\??$", "status"),
        (r"\bnuke (contacts|recipients)\b", "clear recipients"),
        (r"^set up gmail\b", "configure gmail"),
        (r"^use gmail\b", "configure gmail"),
        (r"^connect gmail\b", "configure gmail"),
        (r"^use the dry[\s\-]?run\b", "dryrun"),
        (r"^use dry[\s\-]?run\b", "dryrun"),
        (r"^safe send\b", "dryrun"),
        (r"^send to (all|everyone|everybody)\b", "send bulk"),
        (r"^launch( the)? bulk\b", "send bulk"),
        (r"^fire( the)? campaign\b", "send bulk"),
        (r"^check progress\b", "progress"),
        (r"^how many recipients?\??$", "count recipients"),
        (r"^how many contacts?\??$", "count recipients"),
        (r"^count contacts?\??$", "count recipients"),
        (r"^count recipients?\??$", "count recipients"),
        (r"^who (is|do i have)\??$", "list recipients"),
    ]
    out = low
    for pat, repl in rewrites:
        out = re.sub(pat, repl, out)
    return out


def chat_filter_recipients(state: dict, consent: str) -> dict:
    cs = [c for c in state.get("contacts", []) if c.get("consent_status") == consent]
    rows = [[c["email"], (c.get("first_name","")+" "+c.get("last_name","")).strip() or "—", c.get("company","")] for c in cs[:50]]
    suggestion_chips = ["breakdown", "list recipients"]
    if cs and consent in ("opted_in","soft_opt_in","transactional"):
        suggestion_chips.append("new campaign Spring update")
    return {
        "reply": f"**{len(cs)}** recipient(s) with consent `{consent}`." + (" Showing first 50." if len(cs) > 50 else ""),
        "rich": {"type":"table","headers":["email","name","company"],"rows":rows},
        "suggestions": suggestion_chips,
    }


def chat_breakdown(state: dict) -> dict:
    counts = {v: 0 for v in CONSENT_VALUES}
    for c in state.get("contacts", []):
        cs = c.get("consent_status", "unknown")
        counts[cs] = counts.get(cs, 0) + 1
    rows = [[k, counts[k], "sendable" if k in ("opted_in","soft_opt_in","transactional") else "excluded"] for k in CONSENT_VALUES]
    total = sum(counts.values())
    return {
        "reply": f"📊 **Consent breakdown** — {total} total recipient(s).",
        "rich": {"type":"table","headers":["consent","count","status"],"rows":rows},
    }


def chat_find_contact(state: dict, query: str) -> dict:
    q = query.strip().lower()
    contacts = state.get("contacts", [])
    if "@" in q and not q.startswith("@"):
        matches = [c for c in contacts if c["email"].lower() == q]
        if not matches:
            matches = [c for c in contacts if q in c["email"].lower()]
    elif q.startswith("@") or "." in q:
        # domain search
        domain = q.lstrip("@")
        matches = [c for c in contacts if c["email"].lower().endswith("@" + domain) or domain in c["email"].lower()]
    else:
        matches = [c for c in contacts if q in (c.get("first_name","") + " " + c.get("last_name","")).lower() or q in c.get("company","").lower()]
    if not matches:
        return {"reply": f"No recipients match `{query}`."}
    if len(matches) == 1:
        c = matches[0]
        suppressed = next((s for s in state.get("suppression", []) if s["email"] == c["email"]), None)
        lines = [
            f"📇 **{c['email']}**",
            f"- Name: {(c.get('first_name','') + ' ' + c.get('last_name','')).strip() or '—'}",
            f"- Company: {c.get('company','') or '—'}",
            f"- Consent: `{c.get('consent_status','unknown')}`",
            f"- Source: {c.get('source','—')}",
            f"- Tags: {', '.join(c.get('tags', [])) or '—'}",
        ]
        if suppressed:
            lines.append(f"- ⚠️ Suppressed: `{suppressed['reason']}` (via {suppressed['source']})")
        return {"reply": "\n".join(lines)}
    rows = [[c["email"], (c.get("first_name","")+" "+c.get("last_name","")).strip() or "—", c.get("consent_status","")] for c in matches[:50]]
    return {"reply": f"**{len(matches)}** match(es) for `{query}`.", "rich": {"type":"table","headers":["email","name","consent"],"rows":rows}}


def chat_delete_campaign(state: dict, sess: dict, ref: str) -> dict:
    ref_low = ref.strip().lower()
    if not ref_low:
        return {"reply": "Which campaign? Try `delete campaign Spring update` or `delete campaign <id>`."}
    matches = [c for c in state.get("campaigns", []) if c["id"].startswith(ref_low) or c["name"].lower() == ref_low or ref_low in c["name"].lower()]
    if not matches:
        return {"reply": f"No campaign matches `{ref}`. Try `list campaigns`."}
    if len(matches) > 1:
        rows = [[c["name"], c["id"][:8] + "…", c["status"]] for c in matches]
        return {"reply": f"{len(matches)} matches — be more specific.", "rich": {"type":"table","headers":["name","id","status"],"rows":rows}}
    target = matches[0]
    state["campaigns"] = [c for c in state["campaigns"] if c["id"] != target["id"]]
    if sess.get("last_campaign_id") == target["id"]:
        sess["last_campaign_id"] = None
    save_state(state)
    return {"reply": f"🗑️ Deleted campaign **{target['name']}** (`{target['id'][:8]}…`).", "state_dirty": True}


def chat_delete_sender(state: dict, sess: dict, ref: str) -> dict:
    target = _find_sender(state, ref)
    if not target:
        # also try partial label match
        matches = [s for s in state.get("senders", []) if ref.lower() in s["label"].lower() or ref.lower() in s["sender_email"].lower()]
        if len(matches) == 1:
            target = matches[0]
    if not target:
        return {"reply": f"No sender matches `{ref}`. Try `list senders`."}
    if target.get("id") in ("local-dryrun", "local-mailpit"):
        return {"reply": "Built-in senders (dry-run / mailpit) can't be deleted."}
    state["senders"] = [s for s in state["senders"] if s["id"] != target["id"]]
    SECRET_STORE.pop(target["id"], None)
    persist_secrets()
    if sess.get("active_sender_id") == target["id"]:
        sess["active_sender_id"] = None
    save_state(state)
    return {"reply": f"🗑️ Deleted sender **{target['label']}** ({target['sender_email']}).", "state_dirty": True}


def chat_remove_contact(state: dict, email: str) -> dict:
    email = normalize_email(email)
    before = len(state.get("contacts", []))
    state["contacts"] = [c for c in state.get("contacts", []) if c["email"] != email]
    after = len(state["contacts"])
    if after == before:
        return {"reply": f"No recipient with email `{email}`."}
    save_state(state)
    return {"reply": f"🗑️ Removed recipient `{email}`.", "state_dirty": True}


def chat_clear_contacts(state: dict) -> dict:
    n = len(state.get("contacts", []))
    state["contacts"] = []
    save_state(state)
    return {"reply": f"🗑️ Cleared **{n}** recipient(s).", "state_dirty": True}


def chat_suppress(state: dict, email: str, reason: str) -> dict:
    email = normalize_email(email)
    if not valid_email(email):
        return {"reply": f"`{email}` is not a valid email."}
    reason = (reason or "manual").strip().lower()
    existing = next((s for s in state.get("suppression", []) if s["email"] == email), None)
    if existing:
        existing["reason"] = reason
        existing["source"] = "chat"
    else:
        state["suppression"].append({"id": uuid.uuid4().hex, "email": email, "reason": reason, "source": "chat", "created_at": now()})
    audit(state, action="suppression.added", entity_type="suppression", entity_id=email,
          details={"reason": reason})
    save_state(state)
    return {"reply": f"🚫 Suppressed `{email}` — reason `{reason}`.", "state_dirty": True}


def chat_unsuppress(state: dict, email: str) -> dict:
    email = normalize_email(email)
    before = len(state.get("suppression", []))
    state["suppression"] = [s for s in state.get("suppression", []) if s["email"] != email]
    if len(state["suppression"]) == before:
        return {"reply": f"`{email}` was not on the suppression list."}
    audit(state, action="suppression.removed", entity_type="suppression", entity_id=email)
    save_state(state)
    return {"reply": f"✅ Removed `{email}` from the suppression list.", "state_dirty": True}


def chat_apply_template(sess: dict, template: str) -> dict:
    key = template.strip().lower()
    if key not in CHAT_TEMPLATES:
        return {"reply": f"Unknown template `{template}`. Options: {', '.join(CHAT_TEMPLATES.keys())}."}
    tmpl = CHAT_TEMPLATES[key]
    d = sess.setdefault("draft", {})
    d["html_body"] = tmpl["html"]
    d["plain_body"] = tmpl["plain"]
    if tmpl.get("subject"):
        d.setdefault("subject", tmpl["subject"])
    if tmpl.get("purpose"):
        d.setdefault("purpose", tmpl["purpose"])
    if tmpl.get("campaign_type"):
        d.setdefault("campaign_type", tmpl["campaign_type"])
    return {"reply": f"📐 {tmpl['name']}. Subject, HTML and plain bodies set.\n\nUse `show draft` to review, or `save campaign` to persist.", "suggestions": ["show draft","subject Quick note","save campaign"]}


def chat_add_contacts_from_text(state: dict, text: str) -> dict:
    emails = re.findall(r"[A-Za-z0-9._+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9.\-]+", text)
    if not emails:
        return {"reply": "Couldn't find any emails in that. Try `add alice@x.com bob@y.com`."}
    rows = [{"email": e} for e in emails]
    result = upsert_contacts(state, rows, "chat")
    save_state(state)
    return {"reply": f"➕ Added {len(emails)} address(es): imported **{result['imported']}**, updated **{result['updated']}**, skipped **{result['skipped']}**.", "state_dirty": True}


# ---------- Paste-aware compose ----------
EMAIL_FINDER = re.compile(r"[A-Za-z0-9._+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9.\-]+")


def chat_parse_email_paste(raw: str) -> dict | None:
    """Detect a pasted email block.

    Returns dict with optional keys: recipients (list), subject, body.
    Returns None if the text doesn't look like a pasted email.
    """
    lines = raw.split("\n")
    header_rx = re.compile(r"^\s*(to|cc|bcc|recipients|subject|from|reply-to)\s*:\s*(.+?)\s*$", re.IGNORECASE)
    matched_headers = {}
    body_start = 0
    for i, line in enumerate(lines):
        m = header_rx.match(line)
        if m:
            matched_headers[m.group(1).lower()] = m.group(2).strip()
            body_start = i + 1
        elif line.strip() == "" and matched_headers:
            body_start = i + 1
            break
        else:
            # first non-header non-blank line ends header block
            if matched_headers:
                break
            return None  # no headers at all → not an email block
    if not matched_headers:
        return None
    recipients = []
    for hk in ("to", "recipients", "cc", "bcc"):
        if hk in matched_headers:
            recipients += EMAIL_FINDER.findall(matched_headers[hk])
    body = "\n".join(lines[body_start:]).strip()
    out = {"recipients": list(dict.fromkeys(recipients))}  # dedupe, preserve order
    if "subject" in matched_headers:
        out["subject"] = matched_headers["subject"]
    if body:
        out["body"] = body
    return out


def _body_to_html(body: str) -> str:
    """Wrap a plain-text body as HTML, preserving newlines."""
    if "<" in body and ">" in body:
        return body  # already HTML
    escaped = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    paragraphs = ["<p>" + p.replace("\n", "<br>") + "</p>" for p in escaped.split("\n\n") if p.strip()]
    return "\n".join(paragraphs)


def chat_ensure_sender(state: dict, sess: dict) -> dict | None:
    """Ensure there's an active sender, falling back to dryrun. Returns the sender or None."""
    sender = next((s for s in state.get("senders", []) if s["id"] == sess.get("active_sender_id")), None)
    if sender:
        return sender
    dr = next((s for s in state.get("senders", []) if s["provider"] == "dryrun"), None)
    if dr:
        sess["active_sender_id"] = dr["id"]
        return dr
    return None


def chat_compose_and_send(
    state: dict, sess: dict, *,
    recipients: list, subject: str | None, body: str | None,
    send: bool = False, name: str | None = None, campaign_type: str = "transactional",
) -> dict:
    """One-shot: add recipients (as opted_in since user explicitly requested), build a campaign, save+validate, optionally fire bulk."""
    recipients = [normalize_email(e) for e in recipients if valid_email(e)]
    recipients = list(dict.fromkeys(recipients))
    if not recipients:
        return {"reply": "I need at least one valid recipient email. Try `email alice@x.com, bob@y.com subject \"Hi\" body \"...\"`."}

    # Auto-add recipients as opted_in (user explicit ask)
    upsert_contacts(state, [{"email": e, "consent_status": "opted_in"} for e in recipients], "chat-send-to")

    # Ensure a sender
    sender = chat_ensure_sender(state, sess)
    if not sender:
        return {"reply": "No sender available. Run `dryrun` or `configure gmail` first, then try again."}

    # Build draft (uses existing chat draft as fallback for missing pieces)
    draft = dict(sess.get("draft", {}))
    draft.setdefault("name", name or f"Ad-hoc send to {len(recipients)}")
    draft.setdefault("campaign_type", campaign_type)
    draft.setdefault("delay_seconds", 0.2)
    if subject:
        draft["subject"] = subject
    if body:
        # auto-append a polite footer with unsubscribe link if the user didn't include one
        has_unsub = "unsubscribe" in body.lower() or "{{unsubscribe_url}}" in body
        plain_footer = "" if has_unsub else "\n\n---\nUnsubscribe: {{unsubscribe_url}}\n{{physical_address}}"
        html_footer = "" if has_unsub else "\n<hr><p style=\"color:#888;font-size:12px\"><a href=\"{{unsubscribe_url}}\">Unsubscribe</a> · {{physical_address}}</p>"
        draft["plain_body"] = body + plain_footer
        draft["html_body"] = _body_to_html(body) + html_footer
    draft.setdefault("purpose", "Ad-hoc send composed in chat.")
    # If the user gave a body but no subject, derive subject from the first non-empty body line.
    if body and not draft.get("subject"):
        for line in body.splitlines():
            line = line.strip()
            if line:
                derived = re.sub(r"^(hi|hey|hello)[\s,]+\{?\{?first_name\}?\}?[\s,!.]*", "", line, flags=re.IGNORECASE).strip()
                derived = derived or line
                draft["subject"] = (derived[:80] + "…") if len(derived) > 80 else derived
                break
        if not draft.get("subject"):
            draft["subject"] = "A quick note from {{sender_name}}"
    sess["draft"] = draft

    missing = [k for k in ("subject", "html_body") if not draft.get(k)]
    if missing:
        return {
            "reply": "Almost there — still need: " + ", ".join(missing) + ".\nReply with `subject ...` and/or paste the body, or include them inline.",
            "suggestions": ["subject Quick note", "show draft", "save campaign"],
            "state_dirty": True,
        }

    # Save the campaign
    save_resp = chat_save_campaign(state, sess)
    if "Saved" not in save_resp.get("reply", "") and "💾" not in save_resp.get("reply", ""):
        return save_resp

    if not send:
        return {
            "reply": (
                save_resp["reply"] + "\n\n"
                f"📨 Composed for **{len(recipients)}** recipient(s). Run `send bulk` to fire, or `test you@you.com` first."
            ),
            "suggestions": ["send bulk", "test you@yourinbox.com", "show draft"],
            "state_dirty": True,
        }

    # Actually fire the bulk
    bulk_resp = chat_bulk_send(state, sess)
    return {
        "reply": save_resp["reply"] + "\n\n" + bulk_resp.get("reply", ""),
        "suggestions": ["progress", "events", "status"],
        "state_dirty": True,
    }


def chat_next_suggestions(state: dict, sess: dict) -> list:
    """Top 3 contextual next prompts given current state."""
    senders = state.get("senders", [])
    contacts = state.get("contacts", [])
    has_active = bool(sess.get("active_sender_id") and any(s["id"] == sess["active_sender_id"] for s in senders))
    draft = sess.get("draft") or {}
    last_id = sess.get("last_campaign_id")
    last_camp = next((c for c in state.get("campaigns", []) if c["id"] == last_id), None)
    sent_count = sum(1 for r in (last_camp or {}).get("recipients", []) if r.get("status") == "sent") if last_camp else 0
    if not has_active:
        return ["dryrun", "configure gmail", "help"]
    if not contacts:
        return ["add alice@example.com bob@example.com", "list senders", "status"]
    if not draft and not last_camp:
        return ["template newsletter", "new campaign Spring update", "breakdown"]
    if draft:
        if not draft.get("subject"):
            return ["template newsletter", "subject Hi {{first_name}}", "show draft"]
        if not draft.get("purpose"):
            return ["purpose Monthly update for opted-in subscribers", "show draft", "save campaign"]
        if not draft.get("html_body"):
            return ["template newsletter", "html <h2>Hi {{first_name}}</h2><p>...</p>", "show draft"]
        return ["save campaign", "show draft", "template marketing"]
    if last_camp and last_camp.get("status") == "draft":
        return ["validate", "test you@inbox.com", "send bulk"]
    if last_camp and sent_count == 0 and last_camp.get("status") not in ("sent", "completed_with_failures"):
        return ["test you@inbox.com", "send bulk", "progress"]
    return ["progress", "status", "new campaign Follow-up"]


def chat_what_next(state: dict, sess: dict) -> dict:
    suggestions = chat_next_suggestions(state, sess)
    bullets = "\n".join(f"- `{s}`" for s in suggestions)
    return {"reply": f"Based on where you are, you probably want one of:\n{bullets}", "suggestions": suggestions}


def chat_show_failures(state: dict) -> dict:
    failures = [e for e in state.get("events", []) if e.get("event") == "failed"]
    if not failures:
        return {"reply": "✅ No failure events recorded."}
    rows = [[e["created_at"][:19], e["email"], (e.get("reason") or "")[:80]] for e in failures[-25:][::-1]]
    return {"reply": f"⚠️ **{len(failures)}** failure event(s).", "rich": {"type": "table", "headers": ["time", "email", "reason"], "rows": rows}}


def chat_draft_about(sess: dict, topic: str) -> dict:
    """Generate a starter email body from a free-text topic."""
    topic = topic.strip().rstrip(".") or "an update"
    short = topic.title()[:60]
    sess.setdefault("draft", {})
    sess["draft"].setdefault("name", short or "Quick update")
    sess["draft"]["campaign_type"] = sess["draft"].get("campaign_type", "newsletter")
    sess["draft"]["subject"] = short
    sess["draft"]["html_body"] = (
        "<h2>Hi {{first_name}},</h2>\n"
        f"<p>Wanted to share a quick note about <strong>{topic}</strong>.</p>\n"
        "<p>(Replace this paragraph with what you want to say, why it matters, and what to do next.)</p>\n"
        "<p>Thanks,<br>{{sender_name}}</p>\n"
        "<p><a href=\"{{unsubscribe_url}}\">Unsubscribe</a></p>\n"
        "<p style=\"color:#888;font-size:12px\">{{physical_address}}</p>"
    )
    sess["draft"]["plain_body"] = (
        f"Hi {{{{first_name}}}},\n\nWanted to share a quick note about {topic}.\n\n"
        "(Replace this with the body of your message.)\n\nThanks,\n{{sender_name}}\n\n"
        "Unsubscribe: {{unsubscribe_url}}\n{{physical_address}}"
    )
    sess["draft"].setdefault("purpose", f"Sharing about {topic} with opted-in contacts.")
    return {"reply": f"✏️ Drafted a starter email about **{topic}**.\n- Subject: `{short}`\n\n`show draft` to review, `subject …` / `html …` to refine, or `save campaign` when ready.", "suggestions": ["show draft", "save campaign", "template newsletter"]}


def chat_quickstart_start(sess: dict) -> dict:
    sess["pending"] = {"action": "quickstart", "step": "sender"}
    return {"reply": (
        "**Quickstart — let's set up a campaign together.**\n\n"
        "Step 1 / 4: pick a sender.\n"
        "- Reply `dryrun` for a safe rehearsal (no real email), or\n"
        "- `gmail you@gmail.com APPPASSWORD` for a real Gmail account\n"
        "- `cancel` to abort"
    ), "suggestions": ["dryrun", "configure gmail", "cancel"]}


def chat_quickstart_advance(sess: dict, state: dict) -> dict | None:
    """Advance the wizard after a successful step. Returns the next-step prompt, or None if not in wizard."""
    if not (sess.get("pending") or {}).get("action") == "quickstart":
        return None
    step = sess["pending"]["step"]
    if step == "sender" and sess.get("active_sender_id"):
        sess["pending"]["step"] = "contacts"
        return {"reply": (
            "✓ Sender set.\n\n"
            "**Step 2 / 4: add recipients.**\n"
            "- 📎 attach a CSV/XLSX, or\n"
            "- `add alice@example.com bob@example.com` to paste a few directly\n"
            "- Then say `next` to continue"
        ), "suggestions": ["add alice@example.com bob@example.com", "next", "cancel"]}
    if step == "contacts" and state.get("contacts"):
        sess["pending"]["step"] = "compose"
        return {"reply": (
            f"✓ {len(state.get('contacts', []))} recipient(s) loaded.\n\n"
            "**Step 3 / 4: draft the email.**\n"
            "- `template newsletter` — ready-made template\n"
            "- Or describe it: `draft about our new pricing page`\n"
            "- Then `save campaign`"
        ), "suggestions": ["template newsletter", "draft about our launch", "save campaign"]}
    if step == "compose" and sess.get("last_campaign_id"):
        sess["pending"]["step"] = "send"
        return {"reply": (
            "✓ Campaign saved.\n\n"
            "**Step 4 / 4: test, then send.**\n"
            "- `test you@yourinbox.com` — single test email\n"
            "- When the test arrives, `send bulk` to fire the campaign"
        ), "suggestions": ["test you@yourinbox.com", "send bulk", "validate"]}
    return None


def chat_topic_help(topic: str) -> dict:
    topic = (topic or "").strip().lower()
    topics = {
        "gmail": (
            "**Gmail setup**\n"
            "1. Turn on 2-Step Verification at https://myaccount.google.com/security\n"
            "2. Create an App Password at https://myaccount.google.com/apppasswords (16 chars)\n"
            "3. In chat: `gmail you@gmail.com abcd efgh ijkl mnop` — I'll save + verify the SMTP connection.\n"
            "4. If you don't have credentials handy, `configure gmail` walks you through one step at a time."
        ),
        "compose": (
            "**Compose a campaign**\n"
            "- Start with `new campaign Spring update` (or any name)\n"
            "- Then set fields: `subject ...`, `purpose ...`, `html ...`, `plain ...`, `type marketing`, `delay 1.5`\n"
            "- Or load a template: `template newsletter` (also: sales, minimal, transactional)\n"
            "- Variables: `{{first_name}} {{last_name}} {{company}} {{sender_name}} {{physical_address}} {{unsubscribe_url}}`\n"
            "- Then `show draft` and `save campaign`."
        ),
        "send": (
            "**Send flow**\n"
            "1. `test you@yourinbox.com` — single test through the real sender\n"
            "2. When it arrives, `send bulk` — queues all eligible recipients\n"
            "3. `progress` — live status of the current batch"
        ),
        "recipients": (
            "**Recipients**\n"
            "- 📎 paperclip to attach a CSV/XLSX (the `email` column is required)\n"
            "- `add alice@x.com bob@y.com` to add by paste\n"
            "- `list opted_in` / `list bounced` etc. to filter by consent\n"
            "- `find alice@x.com` for a single record · `find @gmail.com` for a domain\n"
            "- `breakdown` for a consent-status counts table\n"
            "- `suppress alice@x.com reason bounced` · `unsuppress alice@x.com`"
        ),
    }
    if topic in topics:
        return {"reply": topics[topic]}
    return {"reply": "Topics: `help gmail`, `help compose`, `help send`, `help recipients`. Or just `help` for the full menu."}


def _safe_math_value(node):
    if isinstance(node, ast.Expression):
        return _safe_math_value(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _safe_math_value(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _safe_math_value(node.left)
        right = _safe_math_value(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            if abs(right) > 10:
                raise ValueError("Exponent too large")
            return left ** right
    raise ValueError("Unsupported expression")


def chat_math_answer(text: str) -> dict | None:
    expr = text.lower().strip()
    replacements = {
        "what is": "",
        "what's": "",
        "calculate": "",
        "compute": "",
        "equals": "",
        "equal to": "",
        "plus": "+",
        "minus": "-",
        "times": "*",
        "multiplied by": "*",
        "x": "*",
        "divided by": "/",
        "over": "/",
    }
    for old, new in replacements.items():
        expr = re.sub(r"\b" + re.escape(old) + r"\b", new, expr)
    expr = expr.strip(" ?.")
    if not re.search(r"\d", expr) or not re.search(r"[+\-*/%]", expr):
        return None
    if not re.fullmatch(r"[0-9+\-*/%().\s]+", expr):
        return None
    try:
        value = _safe_math_value(ast.parse(expr, mode="eval"))
    except Exception:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return {"reply": f"{expr} = **{value}**"}


def chat_general_answer(text: str, state: dict, sess: dict) -> dict | None:
    low = text.lower().strip()

    math_reply = chat_math_answer(text)
    if math_reply:
        return math_reply

    if re.fullmatch(r"(hi|hello|hey|yo|good morning|good afternoon|good evening)[!. ]*", low):
        return {"reply": "Hi. Ask me a normal question, or tell me what you want to do with email campaigns.", "suggestions": chat_next_suggestions(state, sess)}

    if re.fullmatch(r"(thanks|thank you|thx|ok thanks|cool thanks)[!. ]*", low):
        return {"reply": "You're welcome. What should we tackle next?", "suggestions": chat_next_suggestions(state, sess)}

    if re.search(r"\b(can|could|should)\s+you\s+answer\b", low) or ("english" in low and "answer" in low):
        return {
            "reply": (
                "Yes. I should answer normal English, not only exact commands. "
                "I can now handle plain questions, quick explanations, simple math, email advice, and app actions. "
                "For live internet facts or a truly open-ended model brain, this local app still needs an external LLM/API connected."
            ),
            "suggestions": ["what is bulk email?", "how do I avoid spam?", "what is 2 + 2?", "what next"],
        }

    if "who are you" in low or "what are you" in low:
        return {"reply": "I'm the local OmniAI Assistant for this email app. I can answer general questions and also operate the campaign workflow by chat."}

    topic_answers = [
        (("bulk email", "mass email"), "Bulk email means sending one campaign to many recipients. A legitimate bulk send uses consented contacts, truthful sender details, an unsubscribe link, and careful pacing."),
        (("unsubscribe", "opt out", "opt-out"), "An unsubscribe link lets recipients stop future marketing email. For marketing/newsletter sends, this app requires `{{unsubscribe_url}}` in the message body."),
        (("smtp",), "SMTP is the protocol used to send email through a mail server. In this app, Gmail uses `smtp.gmail.com`, port `587`, and STARTTLS."),
        (("gmail app password", "app password"), "A Gmail app password is a 16-character password for apps after 2-Step Verification is enabled. Use that instead of your normal Gmail password."),
        (("spam", "deliverability"), "For better deliverability: send only to opted-in recipients, keep From/Reply-To truthful, include a physical address and unsubscribe link, avoid misleading subjects, and start with small batches."),
        (("bounce", "bounced"), "A bounce means the email could not be delivered. Hard bounces should be suppressed so you do not keep sending to invalid addresses."),
        (("consent", "opted in", "opt-in", "opt in"), "Consent means the recipient has a valid reason to receive the email. This app treats `opted_in`, `soft_opt_in`, and `transactional` as sendable."),
        (("csv", "spreadsheet"), "For recipient uploads, use a CSV/XLSX with an `email` column. Optional columns include `first_name`, `last_name`, `company`, `consent_status`, and `tags`."),
        (("subject line", "email subject"), "A good subject line is specific, truthful, and short enough to scan. Avoid fake urgency or misleading claims."),
        (("plain text", "html email"), "HTML email gives layout and links; plain text is the fallback. Sending both is better for compatibility and deliverability."),
    ]
    for keys, answer in topic_answers:
        if any(key in low for key in keys):
            return {"reply": answer, "suggestions": chat_next_suggestions(state, sess)}

    question_like = bool(re.match(r"^(what|why|how|when|where|who|which|can|could|should|is|are|do|does|did)\b", low)) or "?" in low
    if question_like:
        return {
            "reply": (
                "Short answer: I can help, but this local build does not have a full internet-backed language model attached yet. "
                "Ask me practical questions, calculations, campaign/email questions, or tell me an app task in plain English and I will respond directly."
            ),
            "suggestions": ["help", "what next", "how do I avoid spam?", "what is 12 * 8?"],
        }

    if len(text.split()) >= 4:
        return {
            "reply": (
                "I hear you. I can treat that as a normal message, but I need a clearer ask to give a useful answer. "
                "Try asking it as a question, or say what you want me to do with the campaign."
            ),
            "suggestions": chat_next_suggestions(state, sess),
        }

    return None


# ---------- LLM tool handlers ----------
# These adapt the existing chat_* helpers to the uniform `(state, sess, **kwargs) -> dict` shape
# expected by chat_llm.llm_dispatch. Each returns the same dict shape as the rule-based router.

def _llm_list_recipients(state, sess, consent=None):
    if consent:
        return chat_filter_recipients(state, consent)
    cs = state.get("contacts", [])
    rows = [[c["email"], (c.get("first_name","") + " " + c.get("last_name","")).strip() or "—", c.get("consent_status","")] for c in cs[:50]]
    return {"reply": f"**{len(cs)}** recipient(s) in pool.", "rich": {"type": "table", "headers": ["email","name","consent"], "rows": rows}}


def _llm_list_senders(state, sess):
    ss = state.get("senders", [])
    rows = [[s["label"], s["provider"], s["sender_email"],
             "saved" if s.get("password_configured") else ("n/a" if s["provider"] in ("dryrun","mailpit") else "missing")] for s in ss]
    return {"reply": f"**{len(ss)}** sender(s).", "rich": {"type": "table", "headers": ["label","provider","from","password"], "rows": rows}}


def _llm_list_campaigns(state, sess):
    cs = state.get("campaigns", [])
    rows = [[c["name"], c["campaign_type"], c["status"], len(c.get("recipients", []))] for c in cs[-50:]]
    return {"reply": f"**{len(cs)}** campaign(s).", "rich": {"type": "table", "headers": ["name","type","status","#"], "rows": rows}}


def _llm_list_events(state, sess):
    evs = state.get("events", [])[-20:]
    rows = [[e["created_at"][:19], e["email"], e["event"]] for e in reversed(evs)]
    return {"reply": f"Last **{len(rows)}** event(s).", "rich": {"type": "table", "headers": ["time","email","event"], "rows": rows}}


def _llm_list_suppression(state, sess):
    sup = state.get("suppression", [])
    rows = [[s["email"], s["reason"], s["source"]] for s in sup]
    return {"reply": f"**{len(sup)}** suppression entries.", "rich": {"type": "table", "headers": ["email","reason","source"], "rows": rows}}


def _llm_new_campaign(state, sess, name):
    sess["draft"] = {"name": name or "Untitled chat campaign", "campaign_type": "newsletter", "delay_seconds": 0.2}
    return {"reply": f"📝 Started draft **{sess['draft']['name']}**. Set subject/purpose/html/plain, then `save campaign`."}


def _llm_set_field(state, sess, field, value):
    field_map = {
        "subject": "subject", "html_body": "html_body", "plain_body": "plain_body",
        "type": "campaign_type", "delay_seconds": "delay_seconds",
        "purpose": "purpose", "name": "name",
    }
    if field not in field_map:
        return {"reply": f"Unknown field `{field}`."}
    return chat_set_draft(sess, field_map[field], value)


def _llm_test_connection(state, sess):
    sid_ = sess.get("active_sender_id")
    sender = next((s for s in state["senders"] if s["id"] == sid_), None)
    if not sender:
        return {"reply": "No active sender. Configure Gmail or `use_dryrun` first."}
    ok, msg = test_sender_connection(sender)
    return {"reply": f"{'✅' if ok else '⚠️'} {msg}"}


def _llm_add_contacts(state, sess, emails, consent="opted_in"):
    rows = [{"email": e, "consent_status": consent} for e in emails if "@" in e]
    if not rows:
        return {"reply": "No valid email addresses provided."}
    result = upsert_contacts(state, rows, "chat-llm")
    save_state(state)
    return {
        "reply": f"➕ Added **{result['imported']}** new, updated **{result['updated']}**, skipped **{result['skipped']}** (consent: `{consent}`).",
        "state_dirty": True,
    }


def _llm_send_to(state, sess, recipients, body, subject=None, send_now=True):
    # Reuse the existing chat_compose_and_send pipeline so compliance and footer logic stay identical.
    return chat_compose_and_send(
        state, sess,
        recipients=recipients,
        subject=subject,
        body=body,
        send=bool(send_now),
    )


LLM_HANDLERS = {
    # Inspection
    "status":            lambda state, sess: chat_status(state, sess),
    "list_senders":      _llm_list_senders,
    "list_campaigns":    _llm_list_campaigns,
    "list_events":       _llm_list_events,
    "list_suppression":  _llm_list_suppression,
    "consent_breakdown": lambda state, sess: chat_breakdown(state),
    "show_draft":        lambda state, sess: chat_show_draft(sess, state),
    "progress":          lambda state, sess: chat_progress(state, sess),
    # Recipients
    "list_recipients":   _llm_list_recipients,
    "inspect_contact":   lambda state, sess, query: chat_find_contact(state, query),
    "add_contacts":      _llm_add_contacts,
    "remove_contact":    lambda state, sess, email: chat_remove_contact(state, email),
    "clear_contacts":    lambda state, sess: chat_clear_contacts(state),
    # Suppression
    "suppress":          lambda state, sess, email, reason="manual": chat_suppress(state, email, reason),
    "unsuppress":        lambda state, sess, email: chat_unsuppress(state, email),
    # Senders
    "use_dryrun":        lambda state, sess: chat_save_dryrun(state, sess),
    "configure_gmail":   lambda state, sess, email, app_password, sender_name="": chat_save_gmail(state, sess, email, app_password, sender_name),
    "test_connection":   _llm_test_connection,
    "delete_sender":     lambda state, sess, ref: chat_delete_sender(state, sess, ref),
    # Campaign drafting
    "new_campaign":      _llm_new_campaign,
    "set_field":         _llm_set_field,
    "apply_template":    lambda state, sess, template: chat_apply_template(sess, template),
    "save_campaign":     lambda state, sess: chat_save_campaign(state, sess),
    "delete_campaign":   lambda state, sess, ref: chat_delete_campaign(state, sess, ref),
    # Sending
    "test_send":         lambda state, sess, email: chat_test_send(state, sess, email),
    "bulk_send":         lambda state, sess: chat_bulk_send(state, sess),
    # One-shot
    "send_to":           _llm_send_to,
}


def chat_llm_dispatch(sid: str, message: str) -> dict:
    """Route a user message through the Claude LLM when ANTHROPIC_API_KEY is set."""
    if not (chat_llm and chat_llm.llm_enabled()):
        return None  # caller falls back to regex
    sess = chat_session(sid)
    state = load_state()  # llm_dispatch acquires STATE_LOCK per tool call
    return chat_llm.llm_dispatch(message, sess, state, STATE_LOCK, LLM_HANDLERS)


def chat_dispatch(sid: str, message: str) -> dict:
    sess = chat_session(sid)
    with STATE_LOCK:
        state = load_state()
        text = (message or "").strip()
        if not text:
            return {"reply": "Type a command — try `help` or `status`."}
        low = _norm_phrasing(text.lower())

        if low in ("cancel","stop","never mind","nevermind"):
            sess["pending"] = None
            sess["draft"] = {}
            return {"reply": "Cancelled. What next?"}

        # ---- pending slots ----
        pending = sess.get("pending")
        if pending:
            sess["pending"] = None
            action = pending["action"]
            if action == "gmail_email":
                if "@" not in text:
                    sess["pending"] = pending
                    return {"reply": "That doesn't look like an email. Try again, or `cancel`."}
                sess["pending"] = {"action": "gmail_password", "email": normalize_email(text), "name": pending.get("name","")}
                return {"reply": f"Got `{normalize_email(text)}`. Now paste your 16-character app password.\nGet one at https://myaccount.google.com/apppasswords"}
            if action == "gmail_password":
                return chat_save_gmail(state, sess, pending["email"], text, pending.get("name",""))

        # ---- help / meta ----
        m = re.match(r"^help\s+(gmail|compose|send|recipients?)\s*$", low)
        if m:
            return chat_topic_help(m.group(1))
        if low in ("help","?","h","commands","what can you do","what can i do"):
            return {"reply": CHAT_HELP, "suggestions": ["status","list senders","configure gmail","dryrun","new campaign Spring update","breakdown","help gmail"]}
        if low in ("status","summary","where am i","what's loaded"):
            return chat_status(state, sess)
        if low in ("count recipients","count contacts"):
            return {"reply": f"You have **{len(state.get('contacts', []))}** recipient(s) loaded."}
        if low in ("breakdown","consent breakdown","stats","analyze","report","counts"):
            return chat_breakdown(state)

        # ---- listings ----
        if re.match(r"^(list|show|see)\s+recipients?$", low) or low in ("recipients","contacts"):
            cs = state.get("contacts", [])
            rows = [[c["email"], (c.get("first_name","")+" "+c.get("last_name","")).strip() or "—", c.get("consent_status","")] for c in cs[:50]]
            return {"reply": f"**{len(cs)}** recipients in pool.", "rich": {"type":"table","headers":["email","name","consent"],"rows":rows}}
        # consent-filtered listing: "list opted_in", "show bounced recipients", etc.
        m = re.match(r"^(?:list|show|see)\s+(?:(?:contacts?|recipients?)\s+)?(opted_in|soft_opt_in|transactional|unknown|unsubscribed|bounced|complained)(?:\s+(?:contacts?|recipients?))?$", low)
        if m:
            return chat_filter_recipients(state, m.group(1))
        if re.match(r"^(list|show|see)\s+senders?$", low) or low == "senders":
            ss = state.get("senders", [])
            rows = [[s["label"], s["provider"], s["sender_email"], "saved" if s.get("password_configured") else ("n/a" if s["provider"] in ("dryrun","mailpit") else "missing")] for s in ss]
            return {"reply": f"**{len(ss)}** senders.", "rich": {"type":"table","headers":["label","provider","from","password"],"rows":rows}}
        if re.match(r"^(list|show|see)\s+campaigns?$", low) or low == "campaigns":
            cs = state.get("campaigns", [])
            rows = [[c["name"], c["campaign_type"], c["status"], len(c.get("recipients",[]))] for c in cs[-50:]]
            return {"reply": f"**{len(cs)}** campaigns.", "rich": {"type":"table","headers":["name","type","status","#"],"rows":rows}}
        if low in ("events","log","logs","show events"):
            evs = state.get("events", [])[-20:]
            rows = [[e["created_at"][:19], e["email"], e["event"]] for e in reversed(evs)]
            return {"reply": f"Last **{len(rows)}** events.", "rich": {"type":"table","headers":["time","email","event"],"rows":rows}}
        if low in ("suppression","suppressed","suppress list") or re.match(r"^(list|show|see)\s+(suppress(ion)?|blocked|blocklist)\s*$", low):
            sup = state.get("suppression", [])
            rows = [[s["email"], s["reason"], s["source"]] for s in sup]
            return {"reply": f"**{len(sup)}** entries in suppression list.", "rich": {"type":"table","headers":["email","reason","source"],"rows":rows}}

        # ---- find / inspect contact ----
        m = re.match(r"^(?:find|who is|search|lookup|show)\s+(.+)$", low)
        if m and ("@" in m.group(1) or len(m.group(1).strip()) >= 2):
            target = m.group(1).strip()
            # avoid colliding with "show draft" / "show events" / "show recipients" / "show <consent>"
            reserved = {"draft", "current draft", "events", "logs", "log", "campaigns", "campaign",
                        "senders", "recipients", "contacts", "suppression", "suppressed",
                        "opted_in", "soft_opt_in", "transactional", "unknown", "unsubscribed", "bounced", "complained"}
            if target.split()[0] not in reserved:
                return chat_find_contact(state, target)

        # ---- count by consent ----
        m = re.match(r"^how many\s+(opted_in|soft_opt_in|transactional|unknown|unsubscribed|bounced|complained)\b", low)
        if m:
            consent = m.group(1)
            n = sum(1 for c in state.get("contacts", []) if c.get("consent_status") == consent)
            return {"reply": f"**{n}** recipient(s) with consent `{consent}`."}

        # ---- add via paste ----
        m = re.match(r"^add\s+(?:contacts?\s+|recipients?\s+)?(.+@.+)$", text, re.IGNORECASE | re.DOTALL)
        if m and "@" in m.group(1):
            return chat_add_contacts_from_text(state, m.group(1))

        # ---- delete / remove / clear ----
        m = re.match(r"^(?:remove|delete)\s+(?:contact|recipient)\s+(\S+@\S+)\s*$", text, re.IGNORECASE)
        if m:
            return chat_remove_contact(state, m.group(1))
        if low in ("clear contacts","clear recipients","delete all contacts","delete all recipients","nuke contacts","nuke recipients"):
            return chat_clear_contacts(state)
        m = re.match(r"^(?:delete|remove)\s+campaign\s+(.+)$", text, re.IGNORECASE)
        if m:
            return chat_delete_campaign(state, sess, m.group(1))
        m = re.match(r"^(?:delete|remove)\s+sender\s+(.+)$", text, re.IGNORECASE)
        if m:
            return chat_delete_sender(state, sess, m.group(1))

        # ---- suppression ----
        m = re.match(r"^suppress\s+(\S+@\S+)(?:\s+reason\s+(.+))?$", text, re.IGNORECASE)
        if m:
            return chat_suppress(state, m.group(1), m.group(2) or "manual")
        m = re.match(r"^unsuppress\s+(\S+@\S+)\s*$", text, re.IGNORECASE)
        if m:
            return chat_unsuppress(state, m.group(1))

        # ---- templates ----
        m = re.match(r"^(?:use\s+)?template\s+(\w+)\s*$", low) or re.match(r"^use\s+(\w+)\s+template\s*$", low)
        if m:
            return chat_apply_template(sess, m.group(1))

        # ---- upload hint ----
        if re.match(r"^(upload|import|add)(\s+(csv|recipients|contacts|file))?$", low):
            return {"reply": "📎 Click the paperclip button below the chat input to attach a CSV/XLSX. Required column: `email`."}

        # ---- dryrun sender ----
        if low in ("dryrun","dry run","dry-run","use dryrun","use dry run","safe dry run"):
            return chat_save_dryrun(state, sess)

        # ---- gmail configure ----
        # patterns: "gmail you@x.com PASSWORD..." | "configure gmail" | "set up gmail you@x.com ..."
        gm = re.match(r"^(?:configure\s+|set\s*up\s+|setup\s+|use\s+|connect\s+)?gmail\b(.*)$", text, re.IGNORECASE | re.DOTALL)
        if gm:
            rest = gm.group(1).strip()  # preserves original case for the app password
            tokens = rest.split()
            email = None; password = None
            for t in tokens:
                if "@" in t and email is None:
                    email = t
                else:
                    password = (password + " " + t).strip() if password else t
            if not email:
                sess["pending"] = {"action": "gmail_email"}
                return {"reply": "Sure — what's the Gmail address?"}
            if not password:
                sess["pending"] = {"action": "gmail_password", "email": email}
                return {"reply": f"Got `{email}`. Now paste your 16-character app password.\n\nGet one at https://myaccount.google.com/apppasswords (requires 2-Step Verification)."}
            return chat_save_gmail(state, sess, email, password)

        # ---- smtp ----
        # smtp <host> <port> <user> <password> from <email> [enc starttls|ssl|none]
        sm = re.match(r"^smtp\s+(\S+)\s+(\d+)\s+(\S+)\s+(\S+)(?:\s+from\s+(\S+))?(?:\s+enc\s+(\S+))?$", text, re.IGNORECASE)
        if sm:
            host, port, user, pwd, frm, enc = sm.groups()
            return chat_save_smtp(state, sess, host, port, user, pwd, frm or user, enc or "starttls")

        # ---- use sender / test connection ----
        m = re.match(r"^(?:use|switch to|select)\s+sender\s+(.+)$", text, re.IGNORECASE)
        if m:
            ref = m.group(1).strip()
            s = _find_sender(state, ref)
            if not s:
                return {"reply": f"No sender matches `{ref}`. Try `list senders`."}
            sess["active_sender_id"] = s["id"]
            return {"reply": f"✅ Active sender → **{s['label']}** ({s['sender_email']})."}
        if low in ("test connection","verify","verify sender","verify connection"):
            sid_ = sess.get("active_sender_id")
            sender = next((s for s in state["senders"] if s["id"]==sid_), None)
            if not sender:
                return {"reply": "No active sender. Try `gmail you@x.com YOURAPPPASSWORD` or `dryrun`."}
            ok, msg = test_sender_connection(sender)
            return {"reply": f"{'✅' if ok else '⚠️'} {msg}"}

        # ---- campaign draft setters ----
        m = re.match(r"^new\s+campaign\s*(.*)$", text, re.IGNORECASE) or re.match(r"^create\s+campaign\s*(.*)$", text, re.IGNORECASE)
        if m:
            name = m.group(1).strip() or "Untitled chat campaign"
            sess["draft"] = {"name": name, "campaign_type": "newsletter", "delay_seconds": 0.2}
            return {"reply": f"📝 Started draft **{name}**. Set: `subject ...`, `purpose ...`, `html ...`, `plain ...`. Then `save campaign`.",
                    "suggestions": ["subject Hello {{first_name}}", "purpose Monthly update", "html <h2>Hi {{first_name}}</h2>", "save campaign"]}
        m = re.match(r"^subject\s+(.+)$", text, re.IGNORECASE | re.DOTALL)
        if m: return chat_set_draft(sess, "subject", m.group(1).strip())
        m = re.match(r"^name\s+(.+)$", text, re.IGNORECASE)
        if m: return chat_set_draft(sess, "name", m.group(1).strip())
        m = re.match(r"^purpose\s+(.+)$", text, re.IGNORECASE | re.DOTALL)
        if m: return chat_set_draft(sess, "purpose", m.group(1).strip())
        m = re.match(r"^type\s+(\S+)$", text, re.IGNORECASE)
        if m:
            t = m.group(1).lower()
            if t not in CAMPAIGN_TYPES:
                return {"reply": f"Type must be one of: {', '.join(CAMPAIGN_TYPES)}."}
            return chat_set_draft(sess, "campaign_type", t)
        m = re.match(r"^html\s+(.+)$", text, re.IGNORECASE | re.DOTALL)
        if m: return chat_set_draft(sess, "html_body", m.group(1).strip())
        m = re.match(r"^plain\s+(.+)$", text, re.IGNORECASE | re.DOTALL)
        if m: return chat_set_draft(sess, "plain_body", m.group(1).strip())
        m = re.match(r"^delay\s+([\d.]+)$", text, re.IGNORECASE)
        if m: return chat_set_draft(sess, "delay_seconds", m.group(1))

        if low in ("show draft","draft","current draft"):
            return chat_show_draft(sess, state)
        if low in ("clear draft","reset draft"):
            sess["draft"] = {}
            return {"reply": "🗑️ Draft cleared."}
        if low in ("save campaign","save","persist"):
            return chat_save_campaign(state, sess)

        # ---- sending ----
        m = re.match(r"^test\s+(\S+@\S+)$", text, re.IGNORECASE) or re.match(r"^send\s+test\s+(?:to\s+)?(\S+@\S+)$", text, re.IGNORECASE)
        if m:
            return chat_test_send(state, sess, m.group(1).strip())
        if low in ("send bulk","bulk send","send all","send","fire bulk","launch bulk"):
            return chat_bulk_send(state, sess)
        if low in ("progress","check progress","status of last","last status"):
            return chat_progress(state, sess)

        # ---- templates / draft generation ----
        m = re.match(r"^(?:template|use template|apply template)\s+(.+)$", low)
        if m:
            return chat_apply_template(sess, m.group(1).strip())
        m = re.match(r"^(?:draft|write|compose|generate)\s+(?:an?\s+)?(?:email\s+)?about\s+(.+)$", text, re.IGNORECASE)
        if m:
            return chat_draft_about(sess, m.group(1).strip())

        # ---- recipient management ----
        m = re.match(r"^(?:add|import)\s+(?:contacts?\s+|recipients?\s+|emails?\s+)?(.+)$", text, re.IGNORECASE)
        if m and "@" in m.group(1):
            return chat_add_contacts_from_text(state, m.group(1))
        m = re.match(r"^(?:remove|delete)\s+(?:contact\s+|recipient\s+)?(\S+@\S+\.\S+)$", text, re.IGNORECASE)
        if m:
            return chat_remove_contact(state, m.group(1))
        if low in ("clear contacts", "clear recipients", "delete all contacts", "wipe contacts"):
            return chat_clear_contacts(state)
        m = re.match(r"^(?:suppress|block)\s+(\S+@\S+\.\S+)(?:\s+reason\s+(\w+))?$", text, re.IGNORECASE)
        if m:
            return chat_suppress(state, m.group(1), m.group(2) or "manual")
        m = re.match(r"^(?:unsuppress|unblock|allow)\s+(\S+@\S+\.\S+)$", text, re.IGNORECASE)
        if m:
            return chat_unsuppress(state, m.group(1))

        # ---- delete sender / campaign ----
        m = re.match(r"^(?:delete|remove)\s+sender\s+(.+)$", text, re.IGNORECASE)
        if m:
            return chat_delete_sender(state, sess, m.group(1).strip())
        m = re.match(r"^(?:delete|remove)\s+campaign\s+(.+)$", text, re.IGNORECASE)
        if m:
            return chat_delete_campaign(state, sess, m.group(1).strip())

        # ---- failures / errors / bounces ----
        if low in ("failures", "errors", "what failed", "bounces", "any failures", "any errors", "any bounces", "fails", "show failures", "show errors"):
            return chat_show_failures(state)

        # ---- search / find ----
        m = re.match(r"^(?:find|search|lookup|search for|look up)\s+(.+)$", text, re.IGNORECASE)
        if m:
            return chat_find_contact(state, m.group(1).strip())

        # ---- next step / what should i do ----
        if low in ("next", "what next", "what's next", "next step", "what now", "what should i do", "what do i do next", "guide me", "hint"):
            return chat_what_next(state, sess)

        # ---- quickstart wizard ----
        if low in ("quickstart", "quick start", "setup", "start", "begin", "guide me through it", "walk me through"):
            return chat_quickstart_start(sess)
        # advance the wizard if active and a meaningful step just happened
        wiz_reply = chat_quickstart_advance(sess, state)
        if wiz_reply:
            # caller already saw their command's reply on a prior turn — this only fires when
            # the user types `next` while sufficient state exists.
            pass  # handled inside chat_quickstart_advance check below

        # ---- loose "set X to Y" / "X = Y" matchers ----
        m = re.match(r"^set\s+(subject|name|purpose|type|html|plain|delay)\s+(?:to\s+|=\s+|as\s+)?(.+)$", text, re.IGNORECASE | re.DOTALL)
        if m:
            field, value = m.group(1).lower(), m.group(2).strip().strip('"').strip("'")
            if field == "type" and value.lower() not in CAMPAIGN_TYPES:
                return {"reply": f"Type must be one of: {', '.join(CAMPAIGN_TYPES)}."}
            field_map = {"html": "html_body", "plain": "plain_body", "type": "campaign_type", "delay": "delay_seconds"}
            return chat_set_draft(sess, field_map.get(field, field), value)
        m = re.match(r"^(subject|name|purpose|type|html|plain|delay)\s*=\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
        if m:
            field, value = m.group(1).lower(), m.group(2).strip().strip('"').strip("'")
            field_map = {"html": "html_body", "plain": "plain_body", "type": "campaign_type", "delay": "delay_seconds"}
            return chat_set_draft(sess, field_map.get(field, field), value)

        # ---- one-shot email / send-to with structured args ----
        # `email a@x.com, b@y.com subject "Hi" body "..."` (also: `send mail to ...`, `mail ...`)
        compose_head = re.match(r"^(email|mail|send\s+mail\s+to|send\s+email\s+to)\b[:\s,]*(.*)$", text, re.IGNORECASE | re.DOTALL)
        if compose_head and EMAIL_FINDER.search(compose_head.group(2) or ""):
            rest = compose_head.group(2)
            quoted = re.findall(r"\"([^\"]+)\"|'([^']+)'", rest)
            quoted = [a or b for a, b in quoted]
            sub_m = re.search(r"subject[:\s]+(?:\"([^\"]+)\"|'([^']+)')", rest, re.IGNORECASE)
            body_m = re.search(r"body[:\s]+(?:\"([^\"]+)\"|'([^']+)')", rest, re.IGNORECASE | re.DOTALL)
            subject = (sub_m.group(1) or sub_m.group(2)) if sub_m else None
            body = (body_m.group(1) or body_m.group(2)) if body_m else None
            if not subject and quoted:
                subject = quoted[0]
            if not body and len(quoted) >= 2:
                body = quoted[1]
            recipients = EMAIL_FINDER.findall(rest)
            # strip recipients out of body if we accidentally captured them in absence of explicit body
            if body is None:
                # any text after recipients that isn't subject="..." → body
                # take the part after the last quoted segment, or fall back to last paragraph
                pass
            send_now = bool(re.search(r"\b(send|fire|launch|go)\b", text.split(":", 1)[0], re.IGNORECASE)) and bool(body) and bool(subject)
            return chat_compose_and_send(state, sess, recipients=recipients, subject=subject, body=body, send=send_now)

        # `send to a@x.com, b@y.com[: body...]`  also: "send this to a, b"
        m = re.match(r"^(?:send|email|mail)\s+(?:(?:this|the\s+(?:above|following))\s+)?to\s+([^\n:]+?)(?:\s*[:\n]\s*(.*))?$", text, re.IGNORECASE | re.DOTALL)
        if m and EMAIL_FINDER.search(m.group(1) or ""):
            recipients = EMAIL_FINDER.findall(m.group(1))
            body = (m.group(2) or "").strip()
            # detect inline `Subject: ...` line at top of body
            subject = None
            if body:
                sm = re.match(r"^\s*Subject\s*:\s*(.+?)\s*\n+(.*)$", body, re.IGNORECASE | re.DOTALL)
                if sm:
                    subject = sm.group(1).strip()
                    body = sm.group(2).strip()
            return chat_compose_and_send(state, sess, recipients=recipients, subject=subject, body=body or None, send=True)

        # ---- pasted email block (multi-line with Subject:/To: headers) ----
        if "\n" in text:
            parsed = chat_parse_email_paste(text)
            if parsed and (parsed.get("recipients") or parsed.get("subject") or parsed.get("body")):
                return chat_compose_and_send(
                    state, sess,
                    recipients=parsed.get("recipients") or [],
                    subject=parsed.get("subject"),
                    body=parsed.get("body"),
                    send=False,
                )

        # ---- bare email = inspect ----
        if re.match(r"^\S+@\S+\.\S+$", text):
            return chat_find_contact(state, text)

        # ---- general English fallback ----
        general = chat_general_answer(text, state, sess)
        if general:
            return general

        # ---- fallback with did-you-mean ----
        tokens = [t for t in re.split(r"\s+", low) if t]
        guesses = []
        for cmd in CHAT_KNOWN:
            head = cmd.split()[0]
            for t in tokens:
                if difflib.SequenceMatcher(None, t, head).ratio() > 0.7 and cmd not in guesses:
                    guesses.append(cmd)
                    break
        if guesses:
            return {
                "reply": f"I didn't recognize `{text[:80]}`. Did you mean **{guesses[0]}**?",
                "suggestions": guesses[:5] + ["help"],
            }
        return {"reply": f"I didn't recognize `{text[:80]}`. Type `help` to see what I can do.", "suggestions": ["help","status","breakdown","configure gmail","dryrun","new campaign Spring update"]}


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
        # Multi-page Campaign Studio routes — each clean URL maps to a static HTML
        # file. Pages that haven't been built yet gracefully fall back to /chat so
        # the sidebar links never 404.
        page_routes = {
            "/":            "index.html",      # Dashboard
            "/dashboard":   "index.html",
            "/providers":   "providers.html",  # Email Providers
            "/contacts":    "contacts.html",   # Contacts (drop-zone + lists)
            "/templates":   "templates.html",
            "/campaigns":   "campaigns.html",
            "/reports":     "reports.html",
            "/suppression": "suppression.html",
            "/settings":    "settings.html",
            "/quick-launch":"quick-launch.html",
            "/live":        "live.html",
            "/chat":        "chat.html",       # Existing AI chat experience
        }
        if parsed.path in page_routes:
            target = ROOT / page_routes[parsed.path]
            self.path = "/" + (page_routes[parsed.path] if target.exists() else "chat.html")
            return super().do_GET()
        if parsed.path == "/api/connection":
            cfg = read_config()
            self.send_json({"ok": True, "config": cfg})
            return
        if parsed.path == "/api/chat-config":
            info = chat_llm.llm_status() if chat_llm else {"sdk_installed": False, "api_key_set": False, "enabled": False, "model": None}
            self.send_json({"ok": True, **info})
            return
        if parsed.path == "/api/audit":
            with STATE_LOCK:
                state = load_state()
                try:
                    limit = int((parse_qs(parsed.query).get("limit", ["100"])[0]) or "100")
                except ValueError:
                    limit = 100
                rows = list(reversed(state.get("audit_log", [])))[:limit]
            self.send_json({"ok": True, "count": len(rows), "audit_log": rows})
            return
        if parsed.path == "/api/crypto-status":
            self.send_json({"ok": True, **crypto_status()})
            return
        if parsed.path == "/api/uploads/last-invalid.csv":
            rows = list(LAST_UPLOAD_INVALID)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["row", "email", "reason"])
            for r in rows:
                w.writerow([r.get("row", ""), r.get("email", ""), r.get("reason", "")])
            body = buf.getvalue().encode("utf-8")
            fname = "invalid-rows-" + (LAST_UPLOAD_NAME or "upload") + ".csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/state":
            with STATE_LOCK:
                state = load_state()
                clean = dict(state)
                clean["senders"] = [public_sender(sender) for sender in state["senders"]]
                self.send_json(clean)
            return

        # ---------- Phase 1 REST endpoints ----------
        if parsed.path == "/api/email-providers/":
            with STATE_LOCK:
                state = load_state()
                self.send_json({"results": [public_email_provider(p) for p in state.get("senders", []) if p.get("provider") not in ("dryrun", "mailpit")]})
            return
        em = re.match(r"^/api/email-providers/([^/]+)/?$", parsed.path)
        if em:
            with STATE_LOCK:
                state = load_state()
                p = next((x for x in state.get("senders", []) if x["id"] == em.group(1)), None)
            if not p:
                self.send_json({"ok": False, "code": "not_found", "message": "Provider not found."}, HTTPStatus.NOT_FOUND); return
            self.send_json(public_email_provider(p)); return

        if parsed.path == "/api/contact-lists/":
            with STATE_LOCK:
                state = load_state()
                rows = []
                for cl in state.get("contact_lists", []):
                    contacts = [c for c in state.get("contacts", []) if c.get("contact_list_id") == cl["id"]]
                    out = public_contact_list(cl)
                    out["contact_count"] = len(contacts)
                    rows.append(out)
            self.send_json({"results": rows}); return
        clm = re.match(r"^/api/contact-lists/([^/]+)/?$", parsed.path)
        if clm:
            with STATE_LOCK:
                state = load_state()
                cl = next((x for x in state.get("contact_lists", []) if x["id"] == clm.group(1)), None)
                if not cl:
                    self.send_json({"ok": False, "code": "not_found", "message": "Contact list not found."}, HTTPStatus.NOT_FOUND); return
                contacts = [c for c in state.get("contacts", []) if c.get("contact_list_id") == cl["id"]]
                out = public_contact_list(cl)
                out["contact_count"] = len(contacts)
                out["valid_count"] = sum(1 for c in contacts if c.get("is_valid", True))
                out["invalid_count"] = sum(1 for c in contacts if not c.get("is_valid", True))
            self.send_json(out); return
        cm = re.match(r"^/api/contact-lists/([^/]+)/contacts/?$", parsed.path)
        if cm:
            list_id = cm.group(1)
            query = parse_qs(parsed.query)
            try:
                limit_q = max(1, min(500, int(query.get("limit", ["100"])[0])))
                offset_q = max(0, int(query.get("offset", ["0"])[0]))
            except ValueError:
                limit_q, offset_q = 100, 0
            search_q = (query.get("search", [""])[0] or "").lower()
            valid_filter = query.get("valid", [None])[0]
            with STATE_LOCK:
                state = load_state()
                cl = next((x for x in state.get("contact_lists", []) if x["id"] == list_id), None)
                if not cl:
                    self.send_json({"ok": False, "code": "not_found", "message": "Contact list not found."}, HTTPStatus.NOT_FOUND); return
                contacts = [c for c in state.get("contacts", []) if c.get("contact_list_id") == list_id]
            if valid_filter is not None:
                want = valid_filter.lower() in ("1", "true", "yes")
                contacts = [c for c in contacts if c.get("is_valid", True) == want]
            if search_q:
                contacts = [c for c in contacts if search_q in c.get("email", "").lower() or search_q in (c.get("first_name", "") + " " + c.get("last_name", "")).lower()]
            total = len(contacts)
            page = contacts[offset_q:offset_q + limit_q]
            self.send_json({"results": page, "count": total, "offset": offset_q, "limit": limit_q}); return

        invm = re.match(r"^/api/contact-lists/([^/]+)/invalid-rows\.csv$", parsed.path)
        if invm:
            list_id = invm.group(1)
            with STATE_LOCK:
                state = load_state()
                contacts = [c for c in state.get("contacts", []) if c.get("contact_list_id") == list_id and not c.get("is_valid", True)]
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["email", "first_name", "last_name", "company", "validation_error"])
            for c in contacts:
                writer.writerow([c.get("email", ""), c.get("first_name", ""), c.get("last_name", ""), c.get("company", ""), c.get("validation_error", "")])
            body = buf.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="invalid-rows-{list_id}.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body); return

        if parsed.path == "/api/suppression-list/":
            with STATE_LOCK:
                state = load_state()
                self.send_json({"results": state.get("suppression", [])})
            return

        if parsed.path == "/api/audit-log/":
            query = parse_qs(parsed.query)
            try:
                lim = max(1, min(500, int(query.get("limit", ["100"])[0])))
            except ValueError:
                lim = 100
            with STATE_LOCK:
                state = load_state()
                entries = state.get("audit_log", [])[-lim:][::-1]
            self.send_json({"results": entries}); return

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

    def do_DELETE(self):
        parsed = urlparse(self.path)
        try:
            # Delete a contact list (and its contacts)
            m = re.match(r"^/api/contact-lists/([^/]+)/?$", parsed.path)
            if m:
                list_id = m.group(1)
                with STATE_LOCK:
                    state = load_state()
                    cl = next((x for x in state.get("contact_lists", []) if x["id"] == list_id), None)
                    if not cl:
                        self.send_json({"ok": False, "code": "not_found"}, HTTPStatus.NOT_FOUND); return
                    state["contact_lists"] = [x for x in state["contact_lists"] if x["id"] != list_id]
                    before = len(state.get("contacts", []))
                    state["contacts"] = [c for c in state.get("contacts", []) if c.get("contact_list_id") != list_id]
                    audit(state, "contact_list_deleted", "contact_list", list_id, {"name": cl.get("name"), "removed_contacts": before - len(state["contacts"])})
                    save_state(state)
                self.send_response(204); self.end_headers(); return
            # Delete a single contact
            m = re.match(r"^/api/contacts/([^/]+)/?$", parsed.path)
            if m:
                cid = m.group(1)
                with STATE_LOCK:
                    state = load_state()
                    c = next((x for x in state.get("contacts", []) if x.get("id") == cid), None)
                    if not c:
                        self.send_json({"ok": False, "code": "not_found"}, HTTPStatus.NOT_FOUND); return
                    state["contacts"] = [x for x in state["contacts"] if x.get("id") != cid]
                    audit(state, "contact_deleted", "contact", cid, {"email": c.get("email"), "list_id": c.get("contact_list_id")})
                    save_state(state)
                self.send_response(204); self.end_headers(); return
            # Delete an email provider
            m = re.match(r"^/api/email-providers/([^/]+)/?$", parsed.path)
            if m:
                pid = m.group(1)
                if pid in ("local-dryrun", "local-mailpit"):
                    self.send_json({"ok": False, "code": "validation_error", "message": "Built-in providers cannot be deleted."}, HTTPStatus.BAD_REQUEST); return
                with STATE_LOCK:
                    state = load_state()
                    p = next((x for x in state.get("senders", []) if x["id"] == pid), None)
                    if not p:
                        self.send_json({"ok": False, "code": "not_found"}, HTTPStatus.NOT_FOUND); return
                    state["senders"] = [x for x in state["senders"] if x["id"] != pid]
                    SECRET_STORE.pop(pid, None)
                    audit(state, "email_provider_deleted", "email_provider", pid, {"sender_email": p.get("sender_email"), "provider": p.get("provider")})
                    save_state(state)
                self.send_response(204); self.end_headers(); return
            # Delete suppression entry
            m = re.match(r"^/api/suppression-list/([^/]+)/?$", parsed.path)
            if m:
                sid = m.group(1)
                with STATE_LOCK:
                    state = load_state()
                    s = next((x for x in state.get("suppression", []) if x["id"] == sid), None)
                    if not s:
                        self.send_json({"ok": False, "code": "not_found"}, HTTPStatus.NOT_FOUND); return
                    state["suppression"] = [x for x in state["suppression"] if x["id"] != sid]
                    audit(state, "suppression_removed", "suppression", sid, {"email": s.get("email")})
                    save_state(state)
                self.send_response(204); self.end_headers(); return
            self.send_json({"ok": False, "code": "not_found", "message": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"ok": False, "code": "server_error", "message": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PATCH(self):
        # Rename a contact list
        parsed = urlparse(self.path)
        try:
            m = re.match(r"^/api/contact-lists/([^/]+)/?$", parsed.path)
            if m:
                payload = read_json(self)
                new_name = (payload.get("name") or "").strip()
                if not new_name:
                    self.send_json({"ok": False, "code": "validation_error", "field": "name", "message": "Name is required."}, HTTPStatus.BAD_REQUEST); return
                with STATE_LOCK:
                    state = load_state()
                    cl = next((x for x in state.get("contact_lists", []) if x["id"] == m.group(1)), None)
                    if not cl:
                        self.send_json({"ok": False, "code": "not_found"}, HTTPStatus.NOT_FOUND); return
                    old = cl.get("name")
                    cl["name"] = new_name
                    cl["updated_at"] = now()
                    audit(state, "contact_list_renamed", "contact_list", cl["id"], {"old": old, "new": new_name})
                    save_state(state)
                self.send_json(public_contact_list(cl)); return
            self.send_json({"ok": False, "code": "not_found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"ok": False, "code": "server_error", "message": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            # ---------- Phase 1 REST POST endpoints ----------
            if parsed.path == "/api/contact-lists/":
                payload = read_json(self)
                name = (payload.get("name") or "").strip()
                if not name:
                    self.send_json({"ok": False, "code": "validation_error", "field": "name", "message": "Name is required."}, HTTPStatus.BAD_REQUEST); return
                new_list = {
                    "id": uuid.uuid4().hex,
                    "user_id": "local",
                    "name": name,
                    "source_type": payload.get("source_type", "manual"),
                    "total_rows": 0, "valid_count": 0, "invalid_count": 0, "duplicate_count": 0,
                    "created_at": now(), "updated_at": now(),
                }
                with STATE_LOCK:
                    state = load_state()
                    state.setdefault("contact_lists", []).append(new_list)
                    audit(state, "contact_list_created", "contact_list", new_list["id"], {"name": name})
                    save_state(state)
                self.send_json(public_contact_list(new_list), HTTPStatus.CREATED); return

            m = re.match(r"^/api/contact-lists/([^/]+)/contacts/?$", parsed.path)
            if m:
                # Add a single contact to a list
                list_id = m.group(1)
                payload = read_json(self)
                email = normalize_email(payload.get("email", ""))
                if not valid_email(email):
                    self.send_json({"ok": False, "code": "validation_error", "field": "email", "message": "Valid email required."}, HTTPStatus.BAD_REQUEST); return
                with STATE_LOCK:
                    state = load_state()
                    cl = next((x for x in state.get("contact_lists", []) if x["id"] == list_id), None)
                    if not cl:
                        self.send_json({"ok": False, "code": "not_found"}, HTTPStatus.NOT_FOUND); return
                    # Dedupe within the list
                    existing = next((c for c in state.get("contacts", []) if c.get("contact_list_id") == list_id and c.get("email") == email), None)
                    if existing:
                        self.send_json({"ok": False, "code": "duplicate", "message": "Email already in this list.", "contact": existing}, HTTPStatus.CONFLICT); return
                    contact = {
                        "id": uuid.uuid4().hex,
                        "contact_list_id": list_id,
                        "email": email,
                        "first_name": payload.get("first_name", ""),
                        "last_name": payload.get("last_name", ""),
                        "company": payload.get("company", ""),
                        "phone": payload.get("phone", ""),
                        "city": payload.get("city", ""),
                        "designation": payload.get("designation", ""),
                        "custom_fields": payload.get("custom_fields", {}),
                        "is_valid": True,
                        "unsubscribed": False,
                        "source": payload.get("source", "manual"),
                        "created_at": now(),
                    }
                    state.setdefault("contacts", []).append(contact)
                    cl["total_rows"] = cl.get("total_rows", 0) + 1
                    cl["valid_count"] = cl.get("valid_count", 0) + 1
                    cl["updated_at"] = now()
                    audit(state, "contact_added", "contact", contact["id"], {"email": email, "list_id": list_id})
                    save_state(state)
                self.send_json(contact, HTTPStatus.CREATED); return

            if parsed.path == "/api/suppression-list/":
                payload = read_json(self)
                email = normalize_email(payload.get("email", ""))
                if not valid_email(email):
                    self.send_json({"ok": False, "code": "validation_error", "field": "email"}, HTTPStatus.BAD_REQUEST); return
                with STATE_LOCK:
                    state = load_state()
                    if any(s["email"] == email for s in state.get("suppression", [])):
                        self.send_json({"ok": False, "code": "duplicate", "message": "Already suppressed."}, HTTPStatus.CONFLICT); return
                    entry = {
                        "id": uuid.uuid4().hex,
                        "user_id": "local",
                        "email": email,
                        "reason": payload.get("reason", "manual"),
                        "source": payload.get("source", "api"),
                        "created_at": now(),
                    }
                    state.setdefault("suppression", []).append(entry)
                    audit(state, "suppression_added", "suppression", entry["id"], {"email": email, "reason": entry["reason"]})
                    save_state(state)
                self.send_json(entry, HTTPStatus.CREATED); return

            if parsed.path == "/api/chat":
                payload = read_json(self)
                sid = payload.get("session_id") or "default"
                message = payload.get("message", "")
                # Try the LLM router first when available; otherwise fall back to regex.
                result = None
                if chat_llm and chat_llm.llm_enabled():
                    try:
                        result = chat_llm_dispatch(sid, message)
                    except Exception as exc:  # noqa: BLE001
                        result = {"reply": f"⚠️ LLM error: {exc}", "_error": True}
                if not result or result.get("_fallback"):
                    result = chat_dispatch(sid, message)
                self.send_json({"ok": True, **result})
                return

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
                    persist_secrets()
                with STATE_LOCK:
                    state = load_state()
                    state["senders"] = [item for item in state["senders"] if item["id"] != sender_id]
                    state["senders"].append(sender)
                    audit(state, action="sender.saved", entity_type="sender", entity_id=sender_id,
                          details={"provider": provider, "sender_email": sender_email, "label": sender.get("label", "")})
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

            if parsed.path == "/api/connection":
                payload = read_json(self)
                # expected keys: smtp_server, port, email, app_password
                cfg = read_config()
                cfg.update({
                    "smtp_server": payload.get("smtp_server") or cfg.get("smtp_server", "smtp.gmail.com"),
                    "port": int(payload.get("port") or cfg.get("port", 587)),
                    "email": payload.get("email") or cfg.get("email", ""),
                })
                if payload.get("app_password"):
                    # do not persist plain text password if not desired, but for DX we save to config
                    cfg["app_password"] = payload.get("app_password")
                write_config(cfg)
                # quick test
                test_sender = {"provider": "gmail", "host": cfg.get("smtp_server"), "port": cfg.get("port"), "username": cfg.get("email"), "encryption": "starttls", "id": "connection-temp", "sender_email": cfg.get("email"), "reply_to": cfg.get("email"), "physical_address": ""}
                if cfg.get("app_password"):
                    SECRET_STORE["connection-temp"] = cfg.get("app_password")
                    persist_secrets()
                ok, message = test_sender_connection(test_sender)
                self.send_json({"ok": ok, "message": message, "config": cfg}, 200 if ok else 400)
                return

            if parsed.path == "/api/quick-send":
                # One-shot send. Accepts either a saved sender (sender_id) OR
                # a new Gmail config (gmail_email + app_password). Then composes
                # the campaign and fires the bulk send.
                payload = read_json(self)
                existing_id = (payload.get("sender_id") or "").strip()
                gmail_email = (payload.get("gmail_email") or "").strip()
                app_password = (payload.get("app_password") or "").strip()
                sender_name = (payload.get("sender_name") or "").strip()
                raw_recipients = payload.get("recipients") or ""
                if isinstance(raw_recipients, list):
                    raw_recipients = "\n".join(raw_recipients)
                recipients = re.findall(r"[A-Za-z0-9._+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9.\-]+", str(raw_recipients))
                seen = set(); recipients = [e for e in (normalize_email(r) for r in recipients) if not (e in seen or seen.add(e))]
                subject = (payload.get("subject") or "").strip() or None
                body = (payload.get("body") or "").strip()

                if not existing_id and (not gmail_email or not app_password):
                    self.send_json({"ok": False, "message": "Either pick a saved sender, or provide a Gmail address + app password."}, HTTPStatus.BAD_REQUEST)
                    return
                if not recipients:
                    self.send_json({"ok": False, "message": "Provide at least one recipient email."}, HTTPStatus.BAD_REQUEST)
                    return
                if not body:
                    self.send_json({"ok": False, "message": "Email body is required."}, HTTPStatus.BAD_REQUEST)
                    return

                with STATE_LOCK:
                    state = load_state()
                    sess = chat_session(payload.get("session_id") or "quick-send")
                    gmail_reply = ""
                    if existing_id:
                        sender = next((s for s in state.get("senders", []) if s["id"] == existing_id), None)
                        if not sender:
                            self.send_json({"ok": False, "stage": "sender", "message": f"Sender '{existing_id}' not found."}, HTTPStatus.BAD_REQUEST)
                            return
                        # For real-provider senders, ensure password is in SECRET_STORE before sending.
                        if sender.get("provider") not in ("dryrun", "mailpit") and not SECRET_STORE.get(existing_id):
                            self.send_json({"ok": False, "stage": "sender",
                                "message": "This sender has no stored password. Re-save it on /providers with the app password."}, HTTPStatus.BAD_REQUEST)
                            return
                        sess["active_sender_id"] = existing_id
                        gmail_reply = f"Using saved sender {sender.get('label') or sender.get('sender_email')}"
                    else:
                        gmail_result = chat_save_gmail(state, sess, gmail_email, app_password, sender_name)
                        gmail_reply = gmail_result.get("reply", "")
                        if "✅" not in gmail_reply and "verified" not in gmail_reply.lower():
                            self.send_json({"ok": False, "stage": "gmail", "message": gmail_reply}, HTTPStatus.BAD_REQUEST)
                            return
                    send_result = chat_compose_and_send(
                        state, sess,
                        recipients=recipients,
                        subject=subject,
                        body=body,
                        send=bool(payload.get("send_now", True)),
                    )

                self.send_json({
                    "ok": True,
                    "gmail": gmail_reply,
                    "send": send_result.get("reply", ""),
                    "recipients": recipients,
                    "subject": subject,
                })
                return

            if parsed.path == "/api/import":
                form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type")})
                upload = form["file"]
                filename = upload.filename or "upload.csv"
                blob = upload.file.read()
                # persist uploaded file for later resume
                try:
                    DATA_DIR.mkdir(exist_ok=True)
                    last_path = DATA_DIR / ("last_upload.xlsx" if filename.lower().endswith(".xlsx") else "last_upload.csv")
                    last_path.write_bytes(blob)
                except Exception:
                    pass
                rows = rows_from_xlsx(blob) if filename.lower().endswith(".xlsx") else rows_from_csv(blob)
                global LAST_UPLOAD_INVALID, LAST_UPLOAD_NAME
                with STATE_LOCK:
                    state = load_state()
                    result = upsert_contacts(state, rows, "xlsx" if filename.lower().endswith(".xlsx") else "csv")
                    LAST_UPLOAD_INVALID = list(result.get("errors", []))
                    LAST_UPLOAD_NAME = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).rsplit(".", 1)[0] or "upload"
                    audit(state, action="contacts.imported", entity_type="upload", entity_id="",
                          details={"filename": filename, "imported": result.get("imported", 0),
                                   "updated": result.get("updated", 0), "skipped": result.get("skipped", 0)})
                    save_state(state)
                payload_out = {"ok": True, **result, "total_rows": len(rows)}
                if LAST_UPLOAD_INVALID:
                    payload_out["invalid_download_url"] = "/api/uploads/last-invalid.csv"
                self.send_json(payload_out)
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
                    audit(state, action="campaign.created", entity_type="campaign", entity_id=campaign["id"],
                          details={"name": campaign["name"], "type": campaign["campaign_type"],
                                   "spam_score": (validation.get("spam") or {}).get("score")})
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
                    audit(state, action=f"campaign.{action}", entity_type="campaign", entity_id=campaign_id,
                          details={"name": campaign["name"], "to": payload.get("test_email") if action == "send-test" else None})
                    save_state(state)
                    self.send_json(result, 200 if result.get("ok") else 400)
                    return

            self.send_json({"ok": False, "message": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"ok": False, "message": str(exc)}, HTTPStatus.BAD_REQUEST)


if __name__ == "__main__":
    DATA_DIR.mkdir(exist_ok=True)
    load_state()
    load_persisted_secrets()  # decrypt SMTP/Gmail credentials from disk into SECRET_STORE
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        print(f"OmniAI Email Shooter running at http://127.0.0.1:{PORT}", flush=True)
    except Exception:
        pass
    server.serve_forever()
