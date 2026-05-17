"""LLM-backed chat dispatcher for OmniAI Email Shooter.

Routes user messages through Claude Opus 4.7 with tool calling — the model
chooses which app action to invoke based on natural language. Adaptive
thinking is enabled. Internet access via the server-side ``web_search`` tool.

Falls back to the rule-based regex router (in ``run_ui.py``) when
``ANTHROPIC_API_KEY`` is unset or the ``anthropic`` SDK is unavailable.

Public surface:
    llm_enabled() -> bool
    llm_status() -> dict
    llm_dispatch(message: str, sess: dict, state: dict, state_lock, handlers: dict) -> dict
"""
from __future__ import annotations

import json
import os
import threading

try:
    import anthropic
    _SDK_OK = True
except ImportError:
    anthropic = None  # type: ignore
    _SDK_OK = False


_CLIENT = None
_CLIENT_LOCK = threading.Lock()

MODEL = "claude-opus-4-7"
MAX_TURNS_IN_LOOP = 8       # cap tool-use rounds per user message
MAX_HISTORY_PAIRS = 20      # cap stored user/assistant pairs per session


def llm_enabled() -> bool:
    """True when the SDK is installed and an API key is configured."""
    return _SDK_OK and bool(os.environ.get("ANTHROPIC_API_KEY"))


def llm_status() -> dict:
    return {
        "sdk_installed": _SDK_OK,
        "api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "model": MODEL if llm_enabled() else None,
        "enabled": llm_enabled(),
    }


def _client():
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None and _SDK_OK:
            _CLIENT = anthropic.Anthropic()
        return _CLIENT


SYSTEM_PROMPT = """You are OmniAI Assistant — an AI agent for a local bulk-email tool with strict consent compliance.

Your role: turn the user's natural-language requests into tool calls that mutate the app state, then summarize the result concisely.

Behavioral rules:
- Use tools to perform every action. Never claim something happened without calling the corresponding tool.
- Recipients added via add_contacts or send_to are marked opted_in (the user is explicitly asking).
- Compliance is automatic — save_campaign and bulk_send enforce it; surface validation failures back to the user verbatim.
- For multi-recipient one-shot sends (the user gives you recipients + a message in one shot), use send_to which combines add + draft + save + bulk_send.
- For external info (news, dates, recent events, definitions), use the web_search tool.
- Chain multiple tool calls in one turn for compound workflows.
- After tool calls, reply in 1–3 sentences with **bold** key numbers. Skip filler preambles.
- If a tool needs missing data (e.g. configure_gmail needs the app password), ask the user briefly.

Reference data:
- Templates: newsletter, sales, minimal, transactional, followup.
- Campaign types: newsletter, marketing, transactional, sales_outreach, job_outreach, follow_up.
- Consent values: opted_in, soft_opt_in, transactional, unknown, unsubscribed, bounced, complained.
- Variables you can drop in subject/body: {{first_name}} {{last_name}} {{company}} {{sender_name}} {{physical_address}} {{unsubscribe_url}}.
"""


# Tool schemas mirror the chat_* helper layer in run_ui.py. Each tool's handler
# is provided by run_ui.py (see LLM_HANDLERS) so this module stays decoupled.
TOOLS = [
    # ---- Inspection ----
    {"name": "status", "description": "Return current state: active sender, recipient count, latest campaign and its status.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "list_senders", "description": "List all configured senders.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "list_campaigns", "description": "List recent campaigns with their status.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "list_events", "description": "List recent send events.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "list_suppression", "description": "Show the suppression list (excluded addresses).",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "consent_breakdown", "description": "Return a count breakdown of recipients by consent status.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "show_draft", "description": "Show the current campaign draft fields.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "progress", "description": "Return live progress of the most recent campaign send.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},

    # ---- Recipients ----
    {"name": "list_recipients", "description": "List loaded recipients. Pass `consent` to filter by consent status.",
     "input_schema": {"type": "object", "properties": {
         "consent": {"type": "string", "enum": ["opted_in", "soft_opt_in", "transactional", "unknown", "unsubscribed", "bounced", "complained"]},
     }, "required": [], "additionalProperties": False}},
    {"name": "inspect_contact", "description": "Look up a single contact by email, partial email, domain (@gmail.com), name, or company.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}},
    {"name": "add_contacts", "description": "Add one or more contacts to the recipient pool. Default consent is opted_in.",
     "input_schema": {"type": "object", "properties": {
         "emails": {"type": "array", "items": {"type": "string"}, "description": "Email addresses to add"},
         "consent": {"type": "string", "enum": ["opted_in", "soft_opt_in", "transactional", "unknown"]},
     }, "required": ["emails"], "additionalProperties": False}},
    {"name": "remove_contact", "description": "Remove a single contact by email.",
     "input_schema": {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"], "additionalProperties": False}},
    {"name": "clear_contacts", "description": "Remove ALL recipients. Destructive — call only when the user explicitly asks to clear contacts.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},

    # ---- Suppression ----
    {"name": "suppress", "description": "Add an email to the suppression list so it is excluded from future sends.",
     "input_schema": {"type": "object", "properties": {
         "email": {"type": "string"},
         "reason": {"type": "string", "description": "Reason for suppression (default 'manual')"},
     }, "required": ["email"], "additionalProperties": False}},
    {"name": "unsuppress", "description": "Remove an email from the suppression list.",
     "input_schema": {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"], "additionalProperties": False}},

    # ---- Sender configuration ----
    {"name": "use_dryrun", "description": "Activate the safe dry-run sender (no real email is delivered).",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "configure_gmail", "description": "Save a Gmail SMTP sender and verify the connection. Needs the Gmail address plus a 16-character App Password from Google.",
     "input_schema": {"type": "object", "properties": {
         "email": {"type": "string"},
         "app_password": {"type": "string", "description": "16-character Google App Password"},
         "sender_name": {"type": "string", "description": "Display name (optional)"},
     }, "required": ["email", "app_password"], "additionalProperties": False}},
    {"name": "test_connection", "description": "Verify the active sender can connect to SMTP.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "delete_sender", "description": "Delete a saved sender by label or email.",
     "input_schema": {"type": "object", "properties": {"ref": {"type": "string", "description": "Sender label or email"}}, "required": ["ref"], "additionalProperties": False}},

    # ---- Campaign drafting ----
    {"name": "new_campaign", "description": "Start a new campaign draft with the given name.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}},
    {"name": "set_field", "description": "Set a single field on the current campaign draft.",
     "input_schema": {"type": "object", "properties": {
         "field": {"type": "string", "enum": ["subject", "html_body", "plain_body", "type", "delay_seconds", "purpose", "name"]},
         "value": {"type": "string"},
     }, "required": ["field", "value"], "additionalProperties": False}},
    {"name": "apply_template", "description": "Apply a pre-built campaign template to the current draft.",
     "input_schema": {"type": "object", "properties": {"template": {"type": "string", "enum": ["newsletter", "sales", "minimal", "transactional", "followup"]}}, "required": ["template"], "additionalProperties": False}},
    {"name": "save_campaign", "description": "Save and validate the current campaign draft. Validates compliance and reports any failing checks.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "delete_campaign", "description": "Delete a campaign by name or ID.",
     "input_schema": {"type": "object", "properties": {"ref": {"type": "string"}}, "required": ["ref"], "additionalProperties": False}},

    # ---- Sending ----
    {"name": "test_send", "description": "Send a single test email of the current campaign to the given address.",
     "input_schema": {"type": "object", "properties": {"email": {"type": "string", "description": "Where to send the test"}}, "required": ["email"], "additionalProperties": False}},
    {"name": "bulk_send", "description": "Fire the bulk send for the most-recently saved campaign. Compliance is enforced here.",
     "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},

    # ---- One-shot compose + send ----
    {"name": "send_to", "description": "One-shot: add recipients (as opted_in), build a campaign draft with the given subject/body, save+validate, and fire the bulk send. Use this when the user gives you recipients AND a message in one request.",
     "input_schema": {"type": "object", "properties": {
         "recipients": {"type": "array", "items": {"type": "string"}, "description": "Email addresses to send to"},
         "subject": {"type": "string", "description": "Email subject (optional — auto-derived from body if missing)"},
         "body": {"type": "string", "description": "Email body — HTML or plain text"},
         "send_now": {"type": "boolean", "description": "True (default) to fire immediately, false to save as draft only"},
     }, "required": ["recipients", "body"], "additionalProperties": False}},
]

# Append the Anthropic-hosted web_search tool so the agent has internet access.
# web_search_20260209 includes dynamic filtering (results are pre-filtered server-side
# before reaching the context window) and is the preferred version on Opus 4.6+.
TOOLS_FULL = TOOLS + [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}]


def _summarize_rich(rich):
    """Compact a `rich` table for the LLM tool result content."""
    if not rich or rich.get("type") != "table":
        return None
    headers = rich.get("headers", [])
    rows = rich.get("rows", [])
    if not rows:
        return f"(table: {', '.join(headers)} — 0 rows)"
    lines = [" | ".join(headers), "-" * 40]
    for r in rows[:25]:
        lines.append(" | ".join(str(c) for c in r))
    if len(rows) > 25:
        lines.append(f"... ({len(rows) - 25} more rows)")
    return "\n".join(lines)


def _run_handler(handler, state, sess, state_lock, kwargs: dict) -> tuple:
    """Execute a tool handler under the state lock. Returns (text, rich, suggestions, state_dirty)."""
    try:
        with state_lock:
            result = handler(state, sess, **kwargs) or {}
    except TypeError as exc:
        return (f"Tool failed: {exc}", None, None, False)
    except Exception as exc:
        return (f"Tool error: {exc}", None, None, False)
    text = result.get("reply", "")
    rich = result.get("rich")
    table_text = _summarize_rich(rich)
    if table_text:
        text = (text + "\n\n" + table_text).strip()
    return (text or "Done.", rich, result.get("suggestions") or [], bool(result.get("state_dirty")))


def llm_dispatch(message: str, sess: dict, state: dict, state_lock, handlers: dict) -> dict:
    """Run one agentic loop against Claude and return the same dict shape as ``chat_dispatch``."""
    if not llm_enabled():
        return {"reply": "LLM mode is not enabled. Set ANTHROPIC_API_KEY to use the AI assistant.", "_fallback": True}
    if not message.strip():
        return {"reply": "Say something."}

    client = _client()

    # Persistent conversation history per session
    messages: list = sess.setdefault("llm_messages", [])
    messages.append({"role": "user", "content": message})

    last_rich = None
    suggestions: list = []
    state_dirty = False
    final_text = ""

    for _ in range(MAX_TURNS_IN_LOOP):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=8000,
                thinking={"type": "adaptive"},
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                tools=TOOLS_FULL,
                messages=messages,
            )
        except anthropic.APIError as exc:  # type: ignore[attr-defined]
            messages.pop()  # remove the user message we just added
            return {"reply": f"⚠️ LLM call failed: {exc}", "_error": True}

        # Always preserve the full assistant content (thinking + tool_use blocks must be kept)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    final_text += block.text
            break

        if response.stop_reason == "pause_turn":
            # Server-side tools may pause for resumption — re-send to continue.
            continue

        if response.stop_reason != "tool_use":
            # refusal / max_tokens / stop_sequence — just surface whatever text we have
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    final_text += block.text
            break

        # Execute every tool_use block in this turn and reply with tool_results
        tool_results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            tool_name = block.name
            tool_input = block.input or {}

            if tool_name == "web_search":
                # Server-side; Anthropic executed it. Nothing for us to do.
                continue

            handler = handlers.get(tool_name)
            if not handler:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": f"Unknown tool: {tool_name}",
                    "is_error": True,
                })
                continue

            try:
                text, rich, sugg, dirty = _run_handler(handler, state, sess, state_lock, tool_input)
            except Exception as exc:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": f"Tool error: {exc}",
                    "is_error": True,
                })
                continue

            if rich:
                last_rich = rich
            if sugg:
                suggestions = sugg
            if dirty:
                state_dirty = True

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": text,
            })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        else:
            # Only web_search was used (server-side); loop again to get final response
            continue

    # Trim history to keep memory bounded (each pair = user + assistant)
    if len(messages) > MAX_HISTORY_PAIRS * 2:
        messages[:] = messages[-MAX_HISTORY_PAIRS * 2:]

    out = {"reply": final_text or "Done."}
    if last_rich:
        out["rich"] = last_rich
    if suggestions:
        out["suggestions"] = suggestions
    if state_dirty:
        out["state_dirty"] = True
    return out
