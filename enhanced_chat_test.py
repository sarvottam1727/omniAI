"""Targeted smoke test for the enhanced chat capabilities.

Verifies every new intent: phrasing normalization, consent filters, breakdown,
find/inspect, count by consent, add via paste, remove contact, delete campaign,
delete sender, suppress/unsuppress, templates, topic help, did-you-mean fallback.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import uuid


BASE = os.environ.get("OMNIAI_BASE", "http://127.0.0.1:5173")
SID = "enh-" + uuid.uuid4().hex[:8]


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def upload_csv(blob, name="enh.csv"):
    boundary = "----enh" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/api/import", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def chat(msg):
    r = post("/api/chat", {"session_id": SID, "message": msg})
    short = (r.get("reply") or "")[:120].replace("\n", " ⏎ ")
    rich = "+table" if r.get("rich", {}).get("type") == "table" else ""
    print(f"> {msg!r:60s}  →  {short}  {rich}")
    return r


def expect(cond, msg):
    if not cond:
        print(f"  ✗ FAIL: {msg}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"  ✓ {msg}")


def main() -> int:
    print(f"=== enhanced chat smoke against {BASE} · session {SID} ===\n")

    # seed contacts so the filters have something to work with
    csv_blob = (
        "email,first_name,last_name,company,consent_status\n"
        "alice@example.com,Alice,Walker,Acme,opted_in\n"
        "bob@gmail.com,Bob,Stone,Globex,opted_in\n"
        "carol@example.com,Carol,Reed,Initech,soft_opt_in\n"
        "dan@gmail.com,Dan,Brown,Initrode,transactional\n"
        "noconsent@example.com,No,Consent,Unknown,unknown\n"
        "bouncy@example.com,Bouncy,One,Old,bounced\n"
    ).encode()
    u = upload_csv(csv_blob)
    expect(u.get("ok"), "csv upload ok")

    # 1. normalization
    r = chat("what can you do")
    expect("can drive every action" in r["reply"].lower() or "i can drive" in r["reply"].lower(), "natural 'what can you do' → help")

    r = chat("what's loaded")
    expect("status" in r["reply"].lower() or "active" in r["reply"].lower(), "normalized 'what's loaded' → status")

    r = chat("set up gmail")
    expect("gmail" in r["reply"].lower(), "natural 'set up gmail' → configure gmail")
    chat("cancel")

    # 2. consent filter
    r = chat("list opted_in")
    expect(r.get("rich", {}).get("type") == "table", "list opted_in returns a table")
    expect("opted_in" in r["reply"], "reply mentions opted_in")

    r = chat("show bounced recipients")
    expect("bounced" in r["reply"], "show bounced recipients works")

    # 3. breakdown
    r = chat("breakdown")
    expect(r.get("rich", {}).get("type") == "table", "breakdown returns a table")

    # 4. count by consent
    r = chat("how many opted_in")
    expect("opted_in" in r["reply"], "how many opted_in returns count")

    # 5. find / inspect
    r = chat("find @gmail.com")
    expect("gmail" in r["reply"].lower() or r.get("rich", {}).get("type") == "table", "find by domain")

    r = chat("alice@example.com")
    expect("alice@example.com" in r["reply"].lower(), "bare email triggers inspect")

    r = chat("find carol")
    expect("carol" in r["reply"].lower(), "find by name works")

    # 6. add via paste
    r = chat("add newone@x.com twoofus@y.com")
    expect("added" in r["reply"].lower() or "imported" in r["reply"].lower(), "add via paste works")

    # 7. templates
    chat("dryrun")  # ensure a sender is active
    chat("new campaign Smoke template test")
    r = chat("template newsletter")
    expect("template" in r["reply"].lower() or "applied" in r["reply"].lower(), "newsletter template applied")
    r = chat("show draft")
    expect("draft" in r["reply"].lower() or r.get("rich", {}).get("type") == "table", "show draft shows fields")
    chat("purpose Smoke template test for deletion")
    chat("save campaign")  # persist so we can delete it

    # 8. delete campaign (we just saved one)
    r = chat("delete campaign Smoke template test")
    expect("deleted" in r["reply"].lower(), "delete campaign works")

    # 9. suppress / unsuppress
    r = chat("suppress newone@x.com reason cleanup")
    expect("suppressed" in r["reply"].lower(), "suppress works")
    r = chat("show suppression")
    expect("newone" in r["reply"].lower() or r.get("rich", {}).get("type") == "table", "suppression list shows entry")
    r = chat("unsuppress newone@x.com")
    expect("removed" in r["reply"].lower() or "✅" in r["reply"], "unsuppress works")

    # 10. remove contact
    r = chat("remove contact twoofus@y.com")
    expect("removed" in r["reply"].lower(), "remove contact works")

    # 11. did-you-mean
    r = chat("brkdwn")
    expect("did you mean" in r["reply"].lower() or "didn't recognize" in r["reply"].lower(), "fuzzy fallback fires")
    expect(any("breakdown" in s for s in r.get("suggestions", [])), "did-you-mean suggests breakdown")

    r = chat("sendt to all")
    expect("did you mean" in r["reply"].lower() or "didn't recognize" in r["reply"].lower(), "fuzzy fallback on send variant")

    # 12. topic help
    r = chat("help gmail")
    expect("2-step" in r["reply"].lower() or "app password" in r["reply"].lower(), "topic help: gmail")
    r = chat("help compose")
    expect("subject" in r["reply"].lower() or "draft" in r["reply"].lower(), "topic help: compose")
    r = chat("help send")
    expect("test" in r["reply"].lower() or "bulk" in r["reply"].lower(), "topic help: send")
    r = chat("help recipients")
    expect("csv" in r["reply"].lower() or "consent" in r["reply"].lower(), "topic help: recipients")

    # 13. delete sender (built-in)
    r = chat("delete sender Safe Dry Run")
    expect("can't be deleted" in r["reply"].lower() or "can't delete" in r["reply"].lower() or "built-in" in r["reply"].lower(), "cannot delete built-in dryrun")

    # 14. nuke contacts alias
    r = chat("nuke contacts")
    expect("cleared" in r["reply"].lower(), "nuke contacts alias clears recipients")

    print("\n✅ All enhanced intents passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
