"""End-to-end smoke test against a running OmniAI Email Shooter (dry-run sender).

Walks the exact user journey the new UI exposes:
  1. Upload a CSV of recipients
  2. Save a dry-run sender
  3. Create + validate a campaign
  4. Send a single test email
  5. Send the bulk campaign and watch progress

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


def _request(method, path, *, json_body=None, multipart=None):
    url = BASE + path
    if multipart:
        boundary = "----omniai" + uuid.uuid4().hex
        parts = []
        for name, (filename, blob, ctype) in multipart.items():
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
            parts.append(f"Content-Type: {ctype}\r\n\r\n".encode())
            parts.append(blob)
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    elif json_body is not None:
        body = json.dumps(json_body).encode()
        headers = {"Content-Type": "application/json"}
    else:
        body = None
        headers = {}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "message": exc.read().decode(errors="replace")}


def step(n, title):
    print(f"\n=== Step {n}: {title} ===")


def main() -> int:
    csv_blob = (
        "email,first_name,last_name,company,consent_status,tags\n"
        "alice@example.com,Alice,Walker,Acme,opted_in,newsletter\n"
        "bob@example.com,Bob,Stone,Globex,opted_in,newsletter\n"
        "carol@example.com,Carol,Reed,Initech,soft_opt_in,newsletter\n"
        "dan@example.com,Dan,Brown,Initrode,transactional,vip\n"
        "noconsent@example.com,No,Consent,Unknown Co,unknown,\n"
    ).encode()

    step(1, "upload CSV")
    res = _request("POST", "/api/import", multipart={"file": ("smoke.csv", csv_blob, "text/csv")})
    print(json.dumps(res, indent=2))
    assert res.get("ok"), "upload failed"

    step(2, "save dry-run sender")
    sender_payload = {
        "provider": "dryrun",
        "label": "Smoke dry-run",
        "sender_email": "smoke@omniai.local",
        "sender_name": "Smoke Test",
        "reply_to": "smoke@omniai.local",
        "physical_address": "1 Test Street, Test City",
        "host": "dryrun.local",
        "port": 0,
        "encryption": "none",
        "username": "",
        "password": "",
        "daily_limit": 1000,
    }
    res = _request("POST", "/api/senders", json_body=sender_payload)
    print(json.dumps(res, indent=2))
    assert res.get("ok"), "sender save failed"
    sender_id = res["sender"]["id"]

    step(3, "create + validate campaign")
    campaign_payload = {
        "sender_id": sender_id,
        "name": "Smoke campaign",
        "campaign_type": "newsletter",
        "subject": "Smoke test: OmniAI dry run",
        "purpose": "Automated smoke test of full send pipeline.",
        "html_body": (
            "<h2>Hi {{first_name}},</h2>"
            "<p>This is a dry-run smoke test from {{sender_name}}.</p>"
            "<p><a href=\"{{unsubscribe_url}}\">Unsubscribe</a></p>"
            "<p>{{physical_address}}</p>"
        ),
        "plain_body": "Hi {{first_name}}, this is a dry run.\nUnsubscribe: {{unsubscribe_url}}\n{{physical_address}}",
        "delay_seconds": 0.05,
    }
    res = _request("POST", "/api/campaigns", json_body=campaign_payload)
    print(json.dumps(res["validation"], indent=2))
    assert res.get("ok"), "campaign creation failed"
    cid = res["campaign"]["id"]
    assert res["validation"]["can_send"], "validation says we cannot send"

    step(4, "send test email")
    res = _request("POST", f"/api/campaigns/{cid}/send-test", json_body={"test_email": "selftest@omniai.local"})
    print(json.dumps(res, indent=2))
    assert res.get("ok") and res.get("sent", 0) >= 1, "test send did not succeed"

    step(5, "send bulk")
    res = _request("POST", f"/api/campaigns/{cid}/send", json_body={})
    print(json.dumps(res, indent=2))
    assert res.get("ok"), "bulk send did not start"

    step(6, "wait for completion")
    deadline = time.time() + 30
    final = None
    while time.time() < deadline:
        time.sleep(1.0)
        state = _request("GET", "/api/state")
        camp = next((c for c in state.get("campaigns", []) if c["id"] == cid), None)
        if not camp:
            continue
        statuses = [r.get("status") for r in camp.get("recipients", [])]
        pending = sum(1 for s in statuses if s == "queued")
        sent = sum(1 for s in statuses if s == "sent")
        failed = sum(1 for s in statuses if s == "failed")
        print(f"  status={camp.get('status')} sent={sent} failed={failed} queued={pending}")
        if camp.get("status") in ("sent", "completed_with_failures") and pending == 0:
            final = camp
            break
    if not final:
        print("FAILED: bulk send did not finish in time", file=sys.stderr)
        return 1

    print("\nFINAL CAMPAIGN STATE")
    print(json.dumps({
        "id": final["id"],
        "status": final["status"],
        "recipients": final["recipients"],
        "sent_count": final.get("sent_count"),
    }, indent=2))
    print("\nOK: full journey passed against", BASE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
