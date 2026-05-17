from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORT = 5197
BASE = f"http://127.0.0.1:{PORT}"
DATA = ROOT / "local_data"


def request(path: str, method: str = "GET", payload: dict | None = None, content_type: str = "application/json"):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = content_type
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}


def multipart_upload(path: str, filename: str, body: bytes):
    boundary = "----omniaitest"
    payload = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode("utf-8") + body + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(BASE + path, data=payload, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def expect_http_error(path: str, method: str = "GET", payload: dict | None = None):
    try:
        request(path, method, payload)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    raise AssertionError(f"Expected HTTP error for {method} {path}")


def wait_for_server():
    for _ in range(40):
        try:
            return request("/api/state")
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("Server did not start")


def wait_for_campaign_complete(campaign_id: str):
    for _ in range(80):
        status, state = request("/api/state")
        assert status == 200
        campaign = next((item for item in state["campaigns"] if item["id"] == campaign_id), None)
        assert campaign is not None
        recipients = campaign.get("recipients", [])
        queued = sum(1 for item in recipients if item.get("status") == "queued")
        if campaign.get("status") in {"sent", "completed_with_failures"} and queued == 0:
            return campaign
        time.sleep(0.25)
    raise AssertionError("Campaign did not finish sending")


def main():
    if DATA.exists():
        shutil.rmtree(DATA)

    env = os.environ.copy()
    env["OMNIAI_UI_PORT"] = str(PORT)
    proc = subprocess.Popen([sys.executable, "-u", "run_ui.py"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        status, state = wait_for_server()
        assert status == 200
        assert any(sender["provider"] == "dryrun" for sender in state["senders"])

        status, result = request("/api/senders/test", "POST", {"sender_id": "local-dryrun"})
        assert status == 200 and result["ok"] is True

        status, general = request("/api/chat", "POST", {"session_id": "local-test", "message": "Why can't you answer normal English questions?"})
        assert status == 200
        assert "I didn't recognize" not in general["reply"]
        assert "normal English" in general["reply"]

        status, math = request("/api/chat", "POST", {"session_id": "local-test", "message": "what is 2 + 2?"})
        assert status == 200
        assert "4" in math["reply"]

        status, upload = multipart_upload(
            "/api/import",
            "contacts.csv",
            b"email,first_name,consent_status\nnew@example.com,New,opted_in\nbad-email,Bad,opted_in\nnew@example.com,Dup,opted_in\n",
        )
        assert status == 200
        assert upload["imported"] == 1
        assert upload["skipped"] == 2

        try:
            multipart_upload("/api/import", "bad.csv", b"name\nNo Email\n")
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 400
            assert "email column" in body["message"]
        else:
            raise AssertionError("Missing email column should fail")

        campaign_payload = {
            "sender_id": "local-dryrun",
            "name": "Positive dry run",
            "campaign_type": "newsletter",
            "subject": "Truthful update",
            "purpose": "Testing consent-based newsletter",
            "html_body": '<p>Hello {{first_name}}</p><p><a href="{{unsubscribe_url}}">Unsubscribe</a></p><p>{{physical_address}}</p>',
            "plain_body": "Hello {{first_name}}. Unsubscribe: {{unsubscribe_url}}. {{physical_address}}",
            "delay_seconds": 0,
        }
        status, campaign_result = request("/api/campaigns", "POST", campaign_payload)
        assert status == 200
        assert campaign_result["validation"]["can_send"] is True
        campaign_id = campaign_result["campaign"]["id"]

        status, test_result = request(f"/api/campaigns/{campaign_id}/send-test", "POST", {"test_email": "test@example.com"})
        assert status == 200 and test_result["sent"] == 1

        status, send_result = request(f"/api/campaigns/{campaign_id}/send", "POST", {})
        assert status == 200
        assert send_result["ok"] is True
        campaign = wait_for_campaign_complete(campaign_id)
        assert sum(1 for item in campaign["recipients"] if item["status"] == "sent") >= 1

        bad_payload = dict(campaign_payload)
        bad_payload["html_body"] = "<p>Plain marketing body without the required opt-out link.</p>"
        status, bad_campaign = request("/api/campaigns", "POST", bad_payload)
        assert status == 200
        assert bad_campaign["validation"]["can_send"] is False

        status, gmail = request(
            "/api/senders",
            "POST",
            {
                "provider": "gmail",
                "label": "Gmail without password",
                "sender_email": "user@gmail.com",
                "sender_name": "User",
                "reply_to": "user@gmail.com",
                "physical_address": "123 Compliance Street",
                "host": "smtp.gmail.com",
                "port": 587,
                "encryption": "starttls",
                "daily_limit": 50,
            },
        )
        assert status == 200
        code, body = expect_http_error("/api/senders/test", "POST", {"sender_id": gmail["sender"]["id"]})
        assert code == 400
        assert "app password" in body["message"].lower()

        print("All local positive and negative flows passed.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
