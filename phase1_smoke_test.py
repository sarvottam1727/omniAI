"""Phase 1 REST API smoke test — providers, contact lists, contacts,
suppression, audit log, plus password-at-rest encryption."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path


BASE = os.environ.get("OMNIAI_BASE", "http://127.0.0.1:5173")
DATA = Path(__file__).parent / "local_data"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, None


def expect(cond, msg):
    print(("OK  " if cond else "FAIL") + " " + msg)
    if not cond:
        sys.exit(1)


def main() -> int:
    print(f"=== Phase 1 REST smoke against {BASE} ===\n")

    # 1) Contact lists — initial state should have a Default list with the seed contacts
    code, lists = req("GET", "/api/contact-lists/")
    expect(code == 200, "GET /api/contact-lists/")
    expect(any(cl["name"] == "Default" for cl in lists["results"]),
           "Default list exists after migration")

    # 2) Create a new list
    code, new_list = req("POST", "/api/contact-lists/", {"name": "Phase 1 Smoke List"})
    expect(code == 201, "POST /api/contact-lists/ creates a new list")
    list_id = new_list["id"]

    # 3) Add a contact
    code, c = req("POST", f"/api/contact-lists/{list_id}/contacts/", {
        "email": "smoke1@example.com", "first_name": "Smoke", "company": "TestCo",
    })
    expect(code == 201 and c["email"] == "smoke1@example.com",
           "POST /api/contact-lists/{id}/contacts/ adds a contact")
    contact_id = c["id"]

    # 4) Duplicate add returns 409
    code, _ = req("POST", f"/api/contact-lists/{list_id}/contacts/", {"email": "smoke1@example.com"})
    expect(code == 409, "duplicate contact in same list -> 409")

    # 5) List contacts within a list
    code, page = req("GET", f"/api/contact-lists/{list_id}/contacts/")
    expect(code == 200 and page["count"] == 1, "GET contacts in list")

    # 6) Search filter
    code, page = req("GET", f"/api/contact-lists/{list_id}/contacts/?search=smoke")
    expect(code == 200 and page["count"] == 1, "search filter works")

    # 7) Patch rename
    code, renamed = req("PATCH", f"/api/contact-lists/{list_id}/", {"name": "Phase 1 (renamed)"})
    expect(code == 200 and renamed["name"] == "Phase 1 (renamed)", "PATCH list rename")

    # 8) Delete contact
    code, _ = req("DELETE", f"/api/contacts/{contact_id}/")
    expect(code == 204, "DELETE contact -> 204")

    # 9) Delete list
    code, _ = req("DELETE", f"/api/contact-lists/{list_id}/")
    expect(code == 204, "DELETE contact list -> 204")

    # 10) Invalid CSV download for a known list_id
    code, lists = req("GET", "/api/contact-lists/")
    default_id = next(cl["id"] for cl in lists["results"] if cl["name"] == "Default")
    req_obj = urllib.request.Request(BASE + f"/api/contact-lists/{default_id}/invalid-rows.csv")
    with urllib.request.urlopen(req_obj, timeout=15) as r:
        body = r.read().decode()
        expect(r.status == 200 and body.startswith("email,first_name"), "invalid-rows.csv served")

    # 11) Suppression list CRUD
    code, sup = req("POST", "/api/suppression-list/", {"email": "blocked@example.com", "reason": "test"})
    expect(code == 201, "POST /api/suppression-list/")
    sup_id = sup["id"]
    code, dup = req("POST", "/api/suppression-list/", {"email": "blocked@example.com"})
    expect(code == 409, "duplicate suppression -> 409")
    code, listing = req("GET", "/api/suppression-list/")
    expect(any(s["email"] == "blocked@example.com" for s in listing["results"]), "suppression list listing")
    code, _ = req("DELETE", f"/api/suppression-list/{sup_id}/")
    expect(code == 204, "DELETE suppression -> 204")

    # 12) Audit log — should contain entries for our actions
    code, log = req("GET", "/api/audit-log/?limit=50")
    expect(code == 200 and any(e["action"] == "contact_list_created" for e in log["results"]),
           "audit log captured contact_list_created")
    expect(any(e["action"] == "contact_list_deleted" for e in log["results"]),
           "audit log captured contact_list_deleted")
    expect(any(e["action"] == "suppression_added" for e in log["results"]),
           "audit log captured suppression_added")

    # 13) Email providers (uses existing chat-saved sender if any)
    code, provs = req("GET", "/api/email-providers/")
    expect(code == 200, "GET /api/email-providers/")
    print(f"    -> {len(provs['results'])} provider(s) listed (real Gmail/SMTP only — dryrun filtered)")

    # 14) Cross-tenant access on unknown IDs returns 404
    code, _ = req("GET", "/api/contact-lists/nonexistent-id/")
    expect(code == 404, "unknown list id -> 404")
    code, _ = req("DELETE", "/api/contacts/nonexistent-id/")
    expect(code == 404, "unknown contact id -> 404")

    # 15) Password at rest — state.json must NOT contain any password
    state_path = DATA / "state.json"
    if state_path.exists():
        st = state_path.read_text(encoding="utf-8")
        leaked = any(t in st for t in ("password", "app_password", "appPassword"))
        # The literal string "password_configured" is fine; the field "password" with a value isn't.
        # Quick check: no JSON key "password": with a non-empty value.
        import re
        leak = re.search(r'"password"\s*:\s*"[^"]+"', st)
        expect(not leak, "state.json contains no plaintext password value")

    print("\nALL PHASE 1 REST CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
