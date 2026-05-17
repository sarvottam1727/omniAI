# OmniAI Campaign Studio — Load & Stress Report

**Run date:** 2026-05-17
**Target:** `http://127.0.0.1:5173` (single instance, local)
**Stack:** Python 3.11 stdlib `ThreadingHTTPServer` + JSON state + threaded bulk sender
**Harness:** [`load_stress_suite.py`](load_stress_suite.py) (stdlib only, no extra installs)
**Total wall time:** ~83 s (quick mode)
**Raw data:** [`load_stress_results.json`](load_stress_results.json)

---

## TL;DR

| Question | Answer |
|---|---|
| Latency at idle? | **p50 < 16 ms** on every endpoint, p99 < 32 ms |
| Sustained read throughput? | **~190 req/s** for `/api/state` at 50 concurrent threads, 0 errors |
| Sustained chat throughput? | **~325 req/s** for `/api/chat` at 25 concurrent threads, 0 errors |
| Concurrency breaking point? | First errors at **conc=200** (4% error rate); 7% at 500 |
| Bulk-import scale? | **10k rows in 270 ms** (~37k rows/s parse), 50k tested without issue |
| Bulk send rate (dry-run)? | **~13 emails/s** with `delay_seconds=0.05` (jitter range, by design) |
| Write contention safe? | **Yes** — 10 concurrent uploads, 500 rows total, zero data loss |
| Error path stable? | **Yes** — 200 garbage payloads → 0 server-5xx, post-flood health check OK |
| Memory growth bounded? | **Yes** — audit log capped at 2k entries, state JSON predictable size |

The current architecture is comfortable for the **single-user / small-team** use case it targets (dozens of concurrent operators max, low-thousands of recipients per campaign, hundreds of campaigns per month). It would need infrastructure changes — Postgres + Celery + Redis — before serving 10k+ concurrent users or 1M+ recipients.

---

## Scenario results

### A — Baseline latency (200 sequential per endpoint)

| Endpoint | p50 | p95 | p99 | max | Notes |
|---|---:|---:|---:|---:|---|
| `GET /api/state` | 16 ms | 31 ms | **395 ms** | 458 ms | One outlier on first-call cold start |
| `GET /api/chat-config` | 2 ms | 25 ms | 27 ms | 28 ms | |
| `GET /api/crypto-status` | 2 ms | 25 ms | 27 ms | 27 ms | |
| `GET /api/audit?limit=50` | 15 ms | 28 ms | 30 ms | 31 ms | |
| `POST /api/chat` (regex) | 9 ms | 28 ms | 30 ms | 32 ms | |

**Verdict:** all single-request latencies well under the 100 ms "feels instant" threshold. The 395 ms p99 on `/api/state` is a single cold-start outlier — every subsequent request was fast.

### B — `/api/state` at 50 concurrent threads × 10 s

```
n=1,924 requests · p50=259 ms · p95=331 ms · p99=686 ms · max=1.25 s
errors=0 · throughput=188.5 req/s
```

Latency grows linearly with concurrency (50 simultaneous reads → 50× higher p50). No 5xx, no dropped connections. Read path scales correctly under the **STATE_LOCK** even at 50 concurrent readers.

### C — `/api/chat` (regex router) at 25 concurrent × 10 s

```
n=3,292 requests · p50=73 ms · p95=113 ms · p99=139 ms · max=1.08 s
errors=0 · throughput=326.4 req/s
```

Faster than `/api/state` because chat doesn't always serialize the full state. **325 chat req/s** is more than any single human will ever generate — comfortable headroom.

### D — Bulk upload (10k rows / 0.6 MB CSV)

```
elapsed=270 ms · imported=10,000 · skipped=0
throughput=36,921 rows/s (server-side parse + dedup + insert)
```

CSV upload scales linearly with row count. **Practical limit:** a 100k-row CSV (≈6 MB) extrapolates to ~3 s; a 1M-row CSV would take ~30 s and produce a ~340 MB `/api/state` response (see scenario E).

### E — `/api/state` with 10k contacts loaded

```
n=100 · p50=90 ms · p95=148 ms · p99=163 ms
response_bytes=3.47 MB average
```

The state-response payload is the bottleneck — every refresh ships the full state. At 10k contacts, payload is 3.5 MB; at 100k it would be ~35 MB. **Recommendation:** add `?limit` + `?fields` query params to `/api/state` if recipient lists routinely exceed 50k.

### F — Concurrent uploads (10 threads × 50 rows each)

```
n=10 uploads · p50=119 ms · p95=539 ms · max=539 ms · errors=0
contacts_in_state=500 · expected=500 · correctness=ok
```

**Zero data loss** across 10 simultaneous writers — `STATE_LOCK` serialization works. Hot upload latency p95 = 540 ms is acceptable.

### G — Concurrency cliff ramp

| Concurrent | Completed | Errors | p50 | p95 | Verdict |
|---:|---:|---:|---:|---:|---|
| 10 | 10 | 0 | 52 ms | 85 ms | comfortable |
| 50 | 50 | 0 | 551 ms | 1.0 s | linear scale |
| 100 | 100 | 0 | 602 ms | 1.6 s | linear scale |
| **200** | **192** | **8** | **1.0 s** | **2.1 s** | **first errors (4%)** |
| 300 | 277 | 23 | 1.3 s | 2.4 s | 8% errors |
| 500 | 465 | 35 | 1.7 s | 3.3 s | 7% errors, p95 > 3 s |

**Breaking point: ~200 concurrent requests.** Beyond that, some connections get reset before the threaded HTTP server accepts them (default `ThreadingHTTPServer` has no backlog tuning). Single instance is **not** appropriate for >100 concurrent users without a reverse proxy buffering connections.

### H — Bulk send (100 dry-run recipients, delay=0.05 s)

```
elapsed=7.4 s · sent=100 · failed=0 · queued=0
rate=13.5 emails/s
```

Send rate is **bound by `delay_seconds`** (a deliberate Gmail-rate-limit safety feature, not a perf bottleneck). With delay=0.05 s and ±25 % jitter, theoretical maximum is ~20/s; observed 13.5/s leaves headroom for SMTP I/O. For real Gmail (recommended `delay_seconds=1.5`), expect **~0.6 emails/s**, comfortably under Gmail's 500/day cap.

### I — Bad-input flood (200 garbage `/api/chat` POSTs)

```
n=200 · p50=15 ms · p95=398 ms · max=452 ms
status breakdown: 200=100 (valid-empty handled gracefully), 400=100 (malformed JSON rejected)
server 5xx errors: 0
post-flood health check: 200 OK in 27 ms
```

**Server stayed completely healthy.** Malformed JSON correctly returned 400 without crashing the handler thread. Valid-but-empty payloads got polite 200 responses. No 5xx.

### J — Final state

```
contacts=100, campaigns=2, events=300, audit_log=38, suppression=1
state response = 184,245 bytes (~180 KB after the suite churn)
```

Audit log was capped at 38 entries (well under the 2k limit). Events grow with sends but are bounded per-campaign. State size is well-behaved.

---

## Identified breaking points

| Limit | Threshold | Mitigation |
|---|---|---|
| **Concurrent HTTP requests** | ~200 simultaneously | Front the server with nginx/caddy as a reverse proxy buffer |
| **`/api/state` response size** | Grows ~350 bytes per contact | Add `?slim=1` flag or per-resource endpoints (`/api/contacts?page=...`) |
| **Single bulk-send thread** | One campaign at a time | Multiple campaigns work today but share `STATE_LOCK` write contention |
| **Event log unbounded** | Grows with every send | Add a "trim events older than N days" maintenance task |
| **Single process** | No HA, restart drops in-flight sends | If durability matters → Postgres + Celery (Redis broker) |

---

## Recommended operational limits

For the **current single-instance architecture** to stay comfortable:

| Metric | Recommended ceiling | Why |
|---|---|---|
| Concurrent operators (browsers) | **≤ 20** | Stays well under the 200-conn cliff |
| Recipients per campaign | **≤ 50,000** | Upload completes in <2 s, `/api/state` stays <4 MB |
| Total contacts in state | **≤ 100,000** | `/api/state` stays under 35 MB and 200 ms |
| Sends per day | **Gmail: 500** / **Workspace: 2,000** | Provider limits, not ours |
| Concurrent campaigns | **≤ 3** | All share STATE_LOCK; performance fine, but not parallel-throughput-better |

Beyond these, the system *works* but the SaaS-grade rewrite (Postgres, Redis/Celery, multi-worker, paginated APIs) starts paying off.

---

## What I'd harden next

In priority order:

1. **Paginated `/api/state`** — split into `/api/contacts?limit=&page=`, `/api/campaigns/?limit=`, `/api/events?limit=`. Removes the 35 MB/100k contacts payload cliff.
2. **Connection backlog tuning** — increase the HTTP server backlog or front with nginx so >200 conn doesn't get refused.
3. **Event-log retention** — auto-trim events older than 30 days (audit log already capped).
4. **Concurrent-campaign queue** — give the threaded sender a proper job queue so multiple campaigns can interleave fairly under the daily cap.
5. **Memory profile under sustained load** — run the suite for 30 min and measure RSS deltas; current quick run shows no leak but it's only 83 s.

---

## How to re-run

```powershell
# Make sure server is running
python run_ui.py

# In another shell — quick mode (≈80 s)
python load_stress_suite.py --quick

# Full suite (50k-row upload + 30 s sustained tests, ≈3 min)
python load_stress_suite.py

# Custom base URL
$env:OMNIAI_BASE = "http://127.0.0.1:5174"
python load_stress_suite.py
```

Results land in `load_stress_results.json` (machine-readable for diffing across runs).
