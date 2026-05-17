"""LLM-mode smoke test.

Skips gracefully if ANTHROPIC_API_KEY is not set on the server (i.e. /api/chat-config.enabled is False).

When the key IS set:
- "what's my status?" → status tool
- "use the dry-run sender" → use_dryrun tool
- "send a one-line hello to a@example.com and b@example.com" → send_to tool
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import uuid


BASE = os.environ.get("OMNIAI_BASE", "http://127.0.0.1:5173")
SID = "llm-" + uuid.uuid4().hex[:8]


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode())


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def chat(msg):
    r = post("/api/chat", {"session_id": SID, "message": msg})
    short = (r.get("reply") or "")[:240].replace("\n", " | ")
    print(f"> {msg!r:80s}\n    -> {short}\n")
    return r


def expect(cond, msg):
    if not cond:
        print(f"  FAIL: {msg}", file=sys.stderr)
        sys.exit(1)
    print(f"  OK: {msg}")


def main():
    info = get("/api/chat-config")
    print(f"chat-config: {info}\n")
    if not info.get("enabled"):
        print("SKIP — LLM mode disabled. Set ANTHROPIC_API_KEY before starting the server to enable.")
        return 0

    print(f"=== LLM-mode chat smoke against {BASE} (model: {info.get('model')}, session {SID}) ===\n")

    r = chat("what's my current status?")
    expect("recipient" in (r.get("reply") or "").lower() or "sender" in (r.get("reply") or "").lower(),
           "natural-language status request triggers status tool")

    r = chat("use the dry-run sender")
    expect("dry" in (r.get("reply") or "").lower(),
           "natural-language dry-run request triggers use_dryrun")

    r = chat("Send a one-line hello to alice@example.com and bob@example.com: Hi {{first_name}}, this is a test from OmniAI.")
    reply = (r.get("reply") or "").lower()
    expect("queued" in reply or "sent" in reply or "bulk" in reply or "ready" in reply or "compose" in reply,
           "natural-language multi-recipient send is recognized")

    r = chat("how many recipients have opted_in consent?")
    expect("opted_in" in (r.get("reply") or "").lower() or "opted in" in (r.get("reply") or "").lower(),
           "consent-filter question answered")

    print("\nLLM mode passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
