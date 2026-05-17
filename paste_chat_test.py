"""Smoke test for the paste-aware chat: one-shot `send to N people`, `email ...`,
and multi-line email-block paste.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid


BASE = os.environ.get("OMNIAI_BASE", "http://127.0.0.1:5173")
SID = "paste-" + uuid.uuid4().hex[:8]


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode())


def chat(msg):
    r = post("/api/chat", {"session_id": SID, "message": msg})
    short = (r.get("reply") or "")[:140].replace("\n", " | ")
    rich = " +table" if r.get("rich", {}).get("type") == "table" else ""
    print(f"> {msg!r:80s} -> {short}{rich}")
    return r


def expect(cond, msg):
    if not cond:
        print(f"  FAIL: {msg}", file=sys.stderr)
        sys.exit(1)
    print(f"  OK: {msg}")


def main() -> int:
    print(f"=== paste-aware chat smoke against {BASE} -- session {SID} ===\n")

    chat("dryrun")

    # 1) one-shot: send to two recipients with inline body
    msg = (
        "send to alice@example.com, bob@example.com: "
        "Hi {{first_name}}, this is the announcement. Thanks."
    )
    r = chat(msg)
    reply = r["reply"].lower()
    expect("queued" in reply or "bulk" in reply or "compliance" in reply or "saved" in reply or "composed" in reply,
           "one-shot send-to multi-recipient accepted")

    time.sleep(2)
    chat("progress")

    # 2) `email ...` command with subject + body args
    r = chat('email carol@example.com, dan@example.com subject "Quick note" body "Hi {{first_name}}, this is a quick note."')
    reply = r["reply"].lower()
    expect("composed" in reply or "queued" in reply or "saved" in reply or "added" in reply or "almost there" in reply,
           "email command with explicit subject/body parsed")

    # 3) paste-style email block (To:/Subject: headers + body, multi-line)
    paste = (
        "To: erik@example.com, fran@example.com\n"
        "Subject: Office hours next Tuesday\n"
        "\n"
        "Hi {{first_name}},\n"
        "We're hosting office hours next Tuesday 3pm. Drop in if you have questions.\n"
        "Thanks!"
    )
    r = chat(paste)
    reply = r["reply"].lower()
    expect("composed" in reply or "saved" in reply or "added" in reply or "recipient" in reply or "draft" in reply,
           "paste-style email block parsed")

    state = get("/api/state")
    emails = {c["email"] for c in state.get("contacts", [])}
    expect("erik@example.com" in emails and "fran@example.com" in emails,
           "paste headers added new recipients to state")

    # 4) Just a bare email triggers inspect (NOT compose)
    r = chat("alice@example.com")
    reply = r["reply"].lower()
    expect("alice@example.com" in reply or r.get("rich"),
           "bare email triggers inspect rather than send")

    # 5) Subject is auto-extracted when body starts with 'Subject: ...'
    msg = "send to gina@example.com:\nSubject: Welcome\nHi {{first_name}}, welcome aboard."
    r = chat(msg)
    reply = r["reply"].lower()
    expect("composed" in reply or "queued" in reply or "saved" in reply or "added" in reply,
           "subject-in-body parsed for send-to")
    state = get("/api/state")
    last = state["campaigns"][-1] if state["campaigns"] else None
    expect(last and last.get("subject", "").lower().startswith("welcome"),
           "campaign subject set to 'Welcome' from inline header")

    print("\nALL PASTE-AWARE INTENTS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
