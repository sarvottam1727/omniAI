# Bulk Email System — Architecture

Status: Phase 1 of an incremental build. The spec calls for FastAPI/Django + Postgres + Celery + React. The existing project is **Python stdlib + JSON state + threads + vanilla JS chat UI**, which the user has invested significant iteration in (chat-driven AI assistant, paste-aware compose, Quick Send form, LLM mode, etc.). Throwing it away to rebuild on the recommended stack would discard that work and breach the explicit "Do not break existing project flow" rule.

Decision: **extend the current stack incrementally** until it covers the spec. Where the spec names a heavy dependency (Postgres, Celery), we provide a stdlib-grade equivalent (atomic-write JSON store, in-process queued worker) that satisfies the *contract* (durable storage, queue-based sending, rate-limited workers) without the operational overhead.

---

## 1. Gap analysis — current vs spec

| Capability | Current | Spec |
|---|---|---|
| Email provider config | Senders saved in JSON; password in `SECRET_STORE` (in-memory only) | Multiple per-user providers, **encrypted at rest** |
| Connectivity test | `test_sender_connection()` works | ✓ Same |
| Test email send | `test_send` chat tool works | ✓ Same |
| Bulk upload | `/api/import` handles CSV/XLSX | ✓ + manual paste + duplicates removed + invalid-rows CSV download |
| Contact list management | **Single global pool** | Multiple named lists with source/counts/CRUD |
| Template builder | Inline draft only | **Reusable template library** with spam-score warnings |
| Campaign wizard | Single-step chat flow | **5-step wizard** with review page |
| Sending engine | Threaded `send_campaign` w/ jitter | Same shape; need EmailJob table + retry + auto-pause on failure-rate |
| Queue/worker | In-process thread per campaign | Same; add explicit EmailJob persistence |
| Monitoring | Per-campaign `recipients[]` status | Live dashboard cards + pause/resume/cancel/retry |
| Email logs | `state["events"]` | Same; add per-job attempt count and provider response |
| Suppression | `state["suppression"]` | ✓ Same |
| Audit log | **None** | Required (every action logged with actor/entity/details) |
| Compliance gates | Subject/unsub/address checks in `validate_campaign` | ✓ Same + per-recipient dedup + footer enforcement |
| Multi-user | **No** (single-user local) | Spec implies `user_id` per record |

### Multi-user note

The spec uses `user_id` everywhere. The current app is single-user local. I'm adding `user_id` fields throughout but defaulting them to `"local"` for now — every record will carry a tenant key, so a future auth layer (sessions / OAuth / API tokens) can drop in without a data migration. We can ship Phase N+1 with real auth without breaking schema.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Browser (single-page chat UI + new admin panels)                    │
│  - Chat (existing) · Quick Send (existing)                           │
│  - Providers · Lists · Templates · Campaigns (Phase 2-3 panels)      │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ HTTP/JSON
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│  HTTP API (run_ui.py — ThreadingHTTPServer)                          │
│  - /api/email-providers/*    (Phase 1)                               │
│  - /api/contact-lists/*       (Phase 1)                              │
│  - /api/email-templates/*     (Phase 2)                              │
│  - /api/campaigns/*           (Phase 3)                              │
│  - /api/suppression-list/*    (Phase 1 — renames the existing one)   │
│  - /api/audit-log/*           (Phase 1)                              │
│  - /api/chat, /api/quick-send, /api/import  (existing, untouched)    │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
            ┌────────────┴───────────┬─────────────────┐
            ▼                        ▼                 ▼
   ┌──────────────────┐   ┌─────────────────┐  ┌──────────────────┐
   │  State (JSON)    │   │  SecretStore    │  │  Job Queue       │
   │  state.json      │   │  secrets.bin    │  │  queue.json      │
   │  atomic-write    │   │  Fernet-AES     │  │  background thrd │
   └──────────────────┘   └─────────────────┘  └──────────────────┘
```

### Stack chosen

| Spec recommendation | Chosen here | Reason |
|---|---|---|
| FastAPI / Django | `http.server` (stdlib) | Already in place; no dep churn |
| PostgreSQL | JSON with atomic temp-file rename | Single-process local app; durable enough |
| Redis + Celery | `threading.Thread` worker + persisted EmailJob list | Same contract (queued, rate-limited, restartable) |
| React / Next.js | Vanilla JS + the existing single-page app | Chat UI is the centerpiece; new admin panels added as collapsible sections |
| Fernet | **`cryptography.Fernet`** ✓ | Spec match; key derived from `OMNIAI_SECRET_KEY` env var with a generated dev default |
| pandas/openpyxl | `csv` + the existing `xlsx_*` stdlib parser | Already works |

If you later need horizontal scale or real auth, the data model is shaped so a port to FastAPI + Postgres is a translation, not a redesign.

---

## 3. Database / state schema

Stored in `local_data/state.json` as one JSON document (atomic write). Each top-level array maps to a "table":

```jsonc
{
  "schema_version": 2,
  "email_providers":  [/* UserEmailProvider */],
  "contact_lists":    [/* ContactList */],
  "contacts":         [/* Contact (FK contact_list_id) */],
  "email_templates":  [/* EmailTemplate (Phase 2) */],
  "campaigns":        [/* Campaign */],
  "email_jobs":       [/* EmailJob (Phase 3) */],
  "suppression":      [/* SuppressionList */],
  "audit_log":        [/* EmailAuditLog */],
  "events":           [/* legacy compatibility */]
}
```

The `senders` array (legacy) is migrated on load into `email_providers` with the same IDs, so existing chat sessions don't break. The legacy single `contacts` array is migrated into a `contact_list_id="default-list"` belonging to a `Default` contact list. Both migrations are idempotent.

Secrets live in `local_data/secrets.bin` — a JSON map of `provider_id -> Fernet-encrypted password`. Key: `OMNIAI_SECRET_KEY` env var; if absent, a generated key is stored in `local_data/.secret.key` (gitignored). Passwords never appear in `state.json`, never appear in logs, never appear in API responses.

---

## 4. API surface — full spec mapping

| Phase | Method | Path | Returns |
|---|---|---|---|
| **1** | POST | /api/email-providers/ | created provider (no password) |
| **1** | GET | /api/email-providers/ | list |
| **1** | GET | /api/email-providers/{id}/ | one |
| **1** | PATCH | /api/email-providers/{id}/ | updated |
| **1** | DELETE | /api/email-providers/{id}/ | 204 |
| **1** | POST | /api/email-providers/{id}/test-connection/ | {ok, host_reachable, auth_ok, tls, message} |
| **1** | POST | /api/email-providers/{id}/send-test-email/ | {ok, sent, message} |
| **1** | POST | /api/contact-lists/upload/ | list + summary {valid, invalid, duplicates, final} |
| **1** | GET | /api/contact-lists/ | list |
| **1** | GET | /api/contact-lists/{id}/ | one + contact summary |
| **1** | PATCH | /api/contact-lists/{id}/ | rename |
| **1** | DELETE | /api/contact-lists/{id}/ | 204 |
| **1** | GET | /api/contact-lists/{id}/contacts/ | paged contacts |
| **1** | POST | /api/contact-lists/{id}/contacts/ | add single contact |
| **1** | DELETE | /api/contacts/{id}/ | 204 |
| **1** | GET | /api/contact-lists/{id}/invalid-rows.csv | CSV download |
| **1** | GET | /api/suppression-list/ | list |
| **1** | POST | /api/suppression-list/ | add |
| **1** | DELETE | /api/suppression-list/{id}/ | 204 |
| **1** | GET | /api/audit-log/ | last N entries |
| **2** | * | /api/email-templates/* | template CRUD + preview + send-test |
| **3** | * | /api/campaigns/* | campaign CRUD + launch/pause/resume/cancel/retry + logs + report |

All endpoints scope by `user_id` (currently `"local"`). 404 on cross-tenant access (defense-in-depth even before auth lands).

---

## 5. Queue / worker design (Phase 3)

Each launched campaign generates one `EmailJob` per recipient and persists them to `state.email_jobs[]`. A single background `Thread` per active campaign loops:

```
while campaign.status == "running":
    if failure_rate(jobs) > stop_threshold:  campaign.status = "paused-failure-rate"; break
    if today_sent_count(provider) >= provider.daily_limit:  campaign.status = "paused-daily-cap"; break
    job = next_pending_job()
    if not job:  campaign.status = "completed"; break
    try: smtp_send(...)
    except: job.attempt_count += 1; if job.attempt_count < job.max_retries: requeue; else: job.status = "failed"
    sleep(jittered_delay)
```

Pause/resume/cancel flip the campaign status; the loop checks at the top of each iteration. Restart-safe because jobs are in JSON — on server restart, jobs with status `"running"` are reset to `"queued"` and the worker resumes them.

This is the same contract Celery gives you (durable queue, retry, rate-limit), without Redis. If you later need multi-machine, it's a Celery shim away.

---

## 6. Security

| Concern | Treatment |
|---|---|
| Password at rest | `cryptography.Fernet` (AES-128-CBC + HMAC) — Phase 1 |
| Key management | `OMNIAI_SECRET_KEY` env var; auto-generated to `local_data/.secret.key` if missing |
| Password in API responses | Replaced with `password_configured: bool` flag |
| Password in logs | Never logged — `smtplib`/SMTP code only reads from the secret store |
| Tenant isolation | Every record has `user_id`; API queries filter by current user (`"local"`) |
| Cross-tenant access | 404 on mismatched `user_id` |
| File upload validation | Extension check + size cap (10 MB) + email-column required |
| Malicious template content | HTML body served only inside an SMTP message; the on-disk preview iframe is sandboxed |
| Audit | Every provider/template/campaign/contact mutation logs an entry with actor/entity/action/details |

---

## 7. Error handling

Standard error envelope: `{ok: false, code: "...", message: "...", field?: "..."}`. Codes:

| Code | When |
|---|---|
| `validation_error` | Field validation (invalid email, missing column, port out of range) |
| `not_found` | Cross-tenant access or non-existent ID |
| `smtp_auth_failed` | Wrong app password (Gmail-specific hint added) |
| `smtp_host_unreachable` | Connection timeout / DNS failure |
| `smtp_tls_mismatch` | STARTTLS attempted on a port that doesn't support it |
| `rate_limit_exceeded` | Provider daily/minute cap hit |
| `suppression_hit` | Recipient on the suppression list (campaign-level only — sends are silently filtered) |
| `campaign_already_running` | Launch attempted on a campaign in `running` |
| `worker_unavailable` | Worker thread not alive |
| `compliance_blocked` | Compliance gate failed (missing unsub link, etc.) |

---

## 8. Phased implementation plan

### Phase 1 — Foundation (this session)
- Fernet-encrypted credential store (replaces in-memory SECRET_STORE)
- ContactList data model + Contact ownership + migration of existing global pool to "Default" list
- Audit log model + helper
- REST API endpoints: email-providers, contact-lists, contacts, suppression-list, audit-log
- Minimal "Lists" UI panel in the chat page
- Smoke tests for all new endpoints + regression of `enhanced_chat_test.py`

### Phase 2 — Templates & spam-score warnings
- `email_templates[]` table with name/subject/preview_text/body_html/body_text/variables/spam_score
- Spam-score heuristic (caps ratio, exclamation count, suspicious words, link count, missing unsub)
- Variable expansion now supports `{{city}}`, `{{designation}}`, `{{custom_1..3}}`
- Template library page in UI; "Send test" button per template

### Phase 3 — Campaign wizard + EmailJob queue
- `EmailJob` table with full per-recipient lifecycle
- Per-campaign worker thread; pause/resume/cancel/retry
- Auto-pause on failure-rate > threshold or daily cap hit
- 5-step wizard UI: Sender → List → Template → Sending rules → Review
- Per-campaign monitoring dashboard with progress bar, sent/failed/pending counters

### Phase 4 — Dashboard, exports, logs page
- Dashboard cards: total campaigns, active, sent-today, failed, SMTP health, list count
- Email Logs page filtered by campaign
- Export campaign report (CSV/JSON)
- Schedule-for-later (cron-style)

### Phase 5 — Multi-user auth (optional)
- Session-based or token-based auth
- Promotes `user_id="local"` to real user IDs

---

## 9. Step-by-step Phase 1 implementation (this session)

1. **`secret_store.py`** — new module; Fernet wrapper with key bootstrap
2. **`run_ui.py` schema migration** — bump `schema_version` to 2; migrate legacy `senders` → `email_providers`, legacy `contacts` → list `default-list` rows
3. **REST endpoints** — `/api/email-providers/*`, `/api/contact-lists/*`, `/api/contacts/{id}`, `/api/suppression-list/*`, `/api/audit-log/*`
4. **Audit helper** — `audit(state, action, entity_type, entity_id, details)` called from every mutation
5. **`frontend/static/lists.html`** — new admin panel for contact-list management (kept simple; reuses theme.css)
6. **Tests** — new `phase1_smoke_test.py` exercising each endpoint; rerun `enhanced_chat_test.py` to confirm no chat regressions

Everything in Phase 1 is **additive** — every existing endpoint, chat command, and UI element keeps working unchanged. The migration runs on first load; subsequent loads are no-ops.
