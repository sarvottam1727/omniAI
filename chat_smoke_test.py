"""End-to-end smoke test that drives EVERY step through the /api/chat endpoint only.

Demonstrates the chatbot can:
  1. respond to help/status
  2. activate the dry-run sender
  3. accept CSV upload (via /api/import, since chat can't upload bytes)
  4. start and fill a campaign draft
  5. save + validate
  6. send a test
  7. send the bulk batch

Run against a running server (default http://127.0.0.1:5173). Override with OMNIAI_BASE.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


BASE = os.environ.get("OMNIAI_BASE", "http://127.0.0.1:5173")
SID = "smoke-" + uuid.uuid4().hex[:8]


def _post_json(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "message": exc.read().decode(errors="replace")}


def _get_json(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _upload(blob, name="smoke.csv"):
    boundary = "----chat" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/api/import", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def chat(message):
    """Send one chat message and pretty-print the bot reply."""
    print(f"\n[you]> {message}")
    r = _post_json("/api/chat", {"session_id": SID, "message": message})
    if not r.get("ok"):
        print(f"[bot]! {r.get('message') or r}")
        return r
    reply = r.get("reply", "")
    print(f"[bot]: {reply}")
    if "rich" in r:
        rich = r["rich"]
        if rich and rich.get("type") == "table":
            print(f"        (table: {len(rich.get('rows', []))} rows, headers: {rich.get('headers')})")
    if "suggestions" in r and r["suggestions"]:
        print(f"        (chips: {', '.join(r['suggestions'])})")
    return r


def main() -> int:
    print(f"=== Chat smoke test against {BASE} · session {SID} ===")

    chat("help")
    chat("status")
    chat("dryrun")
    chat("list senders")

    csv_blob = (
        "email,first_name,last_name,company,consent_status\n"
        "alice@example.com,Alice,Walker,Acme,opted_in\n"
        "bob@example.com,Bob,Stone,Globex,opted_in\n"
        "carol@example.com,Carol,Reed,Initech,soft_opt_in\n"
        "dan@example.com,Dan,Brown,Initrode,transactional\n"
        "noconsent@example.com,No,Consent,Unknown,unknown\n"
    ).encode()
    print(f"\n[upload]> sending CSV via /api/import (simulating paperclip)")
    u = _upload(csv_blob)
    print(f"[upload]: imported={u.get('imported')} updated={u.get('updated')} skipped={u.get('skipped')}")
    assert u.get("ok")

    chat("list recipients")
    chat("new campaign Smoke chat campaign")
    chat("subject Chat-driven hello {{first_name}}")
    chat("purpose Verify chatbot can drive the full pipeline.")
    chat("html <h2>Hi {{first_name}}</h2><p>From {{sender_name}}.</p><p><a href=\"{{unsubscribe_url}}\">Unsubscribe</a></p><p>{{physical_address}}</p>")
    chat("plain Hi {{first_name}}, from {{sender_name}}.\nUnsubscribe: {{unsubscribe_url}}\n{{physical_address}}")
    chat("delay 0.05")
    chat("show draft")
    save_resp = chat("save campaign")
    assert "Saved campaign" in save_resp.get("reply", "") or "saved" in save_resp.get("reply", "").lower(), "save failed"

    chat("test selftest@omniai.local")
    bulk_resp = chat("send bulk")
    reply = bulk_resp.get("reply", "")
    assert "queued" in reply or "Bulk" in reply, f"bulk did not queue: {reply}"

    print("\n[wait]> polling state for completion...")
    deadline = time.time() + 25
    final = None
    while time.time() < deadline:
        time.sleep(1.0)
        state = _get_json("/api/state")
        campaigns = state.get("campaigns", [])
        if not campaigns:
            continue
        c = campaigns[-1]
        statuses = [r.get("status") for r in c.get("recipients", [])]
        pending = sum(1 for s in statuses if s == "queued")
        sent = sum(1 for s in statuses if s == "sent")
        failed = sum(1 for s in statuses if s == "failed")
        print(f"  status={c.get('status')} sent={sent} failed={failed} queued={pending}")
        if c.get("status") in ("sent", "completed_with_failures") and pending == 0:
            final = c
            break
    if not final:
        print("FAILED: bulk send did not finish in time", file=sys.stderr)
        return 1

    chat("progress")
    chat("status")
    chat("list campaigns")

    print("\nFINAL CAMPAIGN:", json.dumps({
        "id": final["id"],
        "status": final["status"],
        "sent_count": final.get("sent_count"),
        "recipients": [{"email": r["email"], "status": r["status"]} for r in final.get("recipients", [])],
    }, indent=2))
    print(f"\nOK: chat-driven full journey passed against {BASE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
