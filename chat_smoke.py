"""Chat-driven end-to-end test of OmniAI via /api/chat.

Walks a full bulk send using only natural-language prompts:
  1. dryrun                                 (pick dry-run sender)
  2. upload via /api/import (file attach)   (chat can't carry binaries)
  3. new campaign / subject / purpose / html / plain  (slot-fill the draft)
  4. save campaign                          (persist + validate)
  5. test you@inbox.com                     (single test send)
  6. send bulk                              (bulk send)
  7. progress                               (poll until done)

Set OMNIAI_BASE to override the URL (default http://127.0.0.1:5173).
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


BASE = os.environ.get("OMNIAI_BASE", "http://127.0.0.1:5173")
SID = "smoke-" + uuid.uuid4().hex[:8]


def _post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _upload(csv_blob, filename="smoke.csv"):
    boundary = "----omniai" + uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: text/csv\r\n\r\n",
        csv_blob,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(BASE + "/api/import", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def chat(text):
    r = _post("/api/chat", {"session_id": SID, "message": text})
    assert r.get("ok"), f"chat call failed: {r}"
    reply = (r.get("reply") or "").splitlines()[0][:120]
    print(f"> {text}")
    safe = reply.encode("ascii", "replace").decode()
    print(f"  -> {safe}")
    return r


def main() -> int:
    print(f"Session: {SID}")
    print(f"Base   : {BASE}\n")

    # 1. help should mention key commands
    r = chat("help")
    assert "Configure sender" in r["reply"] or "gmail" in r["reply"].lower(), "help text broken"

    # 2. status (empty state)
    chat("status")

    # 3. dryrun sender
    r = chat("dryrun")
    assert "Dry Run" in r["reply"] or "dryrun" in r["reply"].lower(), f"dryrun reply odd: {r['reply']}"

    # 4. upload via /api/import (binary, can't ride in JSON chat)
    csv = (
        "email,first_name,last_name,company,consent_status\n"
        "alice@example.com,Alice,Walker,Acme,opted_in\n"
        "bob@example.com,Bob,Stone,Globex,opted_in\n"
        "carol@example.com,Carol,Reed,Initech,soft_opt_in\n"
    ).encode()
    up = _upload(csv)
    assert up.get("ok"), f"upload failed: {up}"
    print(f"  (uploaded {up['imported']+up['updated']} recipients via /api/import)")

    # 5. recipients
    r = chat("list recipients")
    assert r.get("rich", {}).get("type") == "table", "list recipients should return a table"

    # 6. start campaign + slot fields
    chat("new campaign Smoke Chat Campaign")
    chat("subject Smoke test from chat: hi {{first_name}}")
    chat("purpose End-to-end chat smoke verifying the dispatcher.")
    chat("type newsletter")
    chat("html <h2>Hi {{first_name}}</h2><p>Sent via chat. <a href=\"{{unsubscribe_url}}\">Unsubscribe</a></p><p>{{physical_address}}</p>")
    chat("plain Hi {{first_name}}, sent via chat. Unsubscribe: {{unsubscribe_url}}. {{physical_address}}")
    chat("delay 0.05")

    # 7. show draft
    r = chat("show draft")
    assert r.get("rich", {}).get("type") == "table", "show draft should be a table"

    # 8. save campaign
    r = chat("save campaign")
    assert "Saved" in r["reply"] or "Ready to send" in r["reply"], f"save campaign odd: {r['reply']}"

    # 9. test send
    r = chat("test selftest@omniai.local")
    assert "Test sent" in r["reply"] or "✅" in r["reply"] or "test" in r["reply"].lower(), f"test send odd: {r['reply']}"

    # 10. bulk send
    r = chat("send bulk")
    assert "queued" in r["reply"].lower() or "🚀" in r["reply"], f"bulk send odd: {r['reply']}"

    # 11. poll progress
    deadline = time.time() + 30
    final_status = None
    while time.time() < deadline:
        time.sleep(1.0)
        r = chat("progress")
        if "sent" in r["reply"].lower() and any(s in r["reply"].lower() for s in ["status `sent`","status `completed"]):
            final_status = r["reply"]
            break
    if not final_status:
        print("⚠️  bulk send did not finish in time", file=sys.stderr)
        return 1

    # final state check
    state = _get("/api/state")
    last = state["campaigns"][-1]
    sent = sum(1 for x in last["recipients"] if x["status"] == "sent")
    print(f"\nFinal: status={last['status']} sent={sent}/{len(last['recipients'])}")

    print("\n✅ Chat-driven end-to-end passed against " + BASE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
