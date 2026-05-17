"""High-level load + stress suite for OmniAI Campaign Studio.

Uses Python stdlib only (no extra installs). Spawns concurrent HTTP traffic
against a running server (default http://127.0.0.1:5173), measures latency
percentiles + error rates, then writes a structured JSON result and prints a
report-ready summary.

Run:
    python load_stress_suite.py                 # full suite
    python load_stress_suite.py --quick         # short version for CI
    OMNIAI_BASE=http://127.0.0.1:5174 python load_stress_suite.py
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed


BASE = os.environ.get("OMNIAI_BASE", "http://127.0.0.1:5173")
RESULTS: dict = {"scenarios": []}


# ---------------- HTTP primitives ----------------

def _request(method: str, path: str, *, body=None, headers=None, timeout=20):
    url = BASE + path
    data = None
    h = {"Connection": "close"}
    if headers:
        h.update(headers)
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    elif isinstance(body, (bytes, bytearray)):
        data = bytes(body)
    elif isinstance(body, str):
        data = body.encode()
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            return {
                "ok": True,
                "status": resp.status,
                "elapsed_ms": (time.perf_counter() - t0) * 1000,
                "bytes": len(payload),
                "body": payload,
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_ms": (time.perf_counter() - t0) * 1000,
            "error": exc.reason,
            "body": (exc.read() if exc.fp else b""),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "elapsed_ms": (time.perf_counter() - t0) * 1000,
            "error": str(exc),
        }


def _multipart(boundary: str, field: str, filename: str, payload: bytes, ctype: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()


def upload_csv(blob: bytes, name: str = "load.csv") -> dict:
    boundary = "----load" + uuid.uuid4().hex
    body = _multipart(boundary, "file", name, blob, "text/csv")
    return _request("POST", "/api/import", body=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


# ---------------- Metrics ----------------

def percentiles(samples: list[float]) -> dict:
    if not samples:
        return {"n": 0}
    s = sorted(samples)
    def pct(p):
        if not s: return 0.0
        idx = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
        return round(s[idx], 2)
    return {
        "n": len(s),
        "min_ms": round(s[0], 2),
        "p50_ms": pct(50),
        "p95_ms": pct(95),
        "p99_ms": pct(99),
        "max_ms": round(s[-1], 2),
        "avg_ms": round(statistics.mean(s), 2),
    }


def record(label: str, **fields):
    print(f"[{label}] " + " · ".join(f"{k}={v}" for k, v in fields.items()))
    RESULTS["scenarios"].append({"label": label, **fields})


# ---------------- Server setup ----------------

def ensure_dryrun_active() -> str:
    """Make sure local-dryrun exists and is the sender for any chat / quick-send tests."""
    state = json.loads(_request("GET", "/api/state")["body"].decode())
    dr = next((s for s in state.get("senders", []) if s["provider"] == "dryrun"), None)
    if not dr:
        # Activate via chat
        _request("POST", "/api/chat", body={"session_id": "load-suite", "message": "dryrun"})
        state = json.loads(_request("GET", "/api/state")["body"].decode())
        dr = next(s for s in state["senders"] if s["provider"] == "dryrun")
    # Patch in a physical_address so campaigns validate cleanly
    if not dr.get("physical_address"):
        _request("POST", "/api/senders", body={
            "id": dr["id"], "provider": "dryrun", "label": dr["label"],
            "sender_email": dr["sender_email"], "sender_name": dr.get("sender_name") or "Load Test",
            "reply_to": dr.get("reply_to") or dr["sender_email"],
            "physical_address": "Load-test address — 1 Probe Street, Pune", "host": "dryrun.local",
            "port": 0, "encryption": "none", "username": "", "daily_limit": 50000,
        })
    return dr["id"]


def reset_contacts():
    """Clear all contacts via the chat router (works in both modes)."""
    _request("POST", "/api/chat", body={"session_id": "load-suite", "message": "clear contacts"})


# ---------------- Scenarios ----------------

def scenario_a_baseline():
    """200 sequential requests, no concurrency — pure latency floor."""
    endpoints = [
        ("GET",  "/api/state",        None),
        ("GET",  "/api/chat-config",  None),
        ("GET",  "/api/crypto-status", None),
        ("GET",  "/api/audit?limit=50", None),
        ("POST", "/api/chat", {"session_id": "baseline", "message": "status"}),
    ]
    for method, path, body in endpoints:
        times = []
        errs = 0
        for _ in range(200):
            r = _request(method, path, body=body)
            if r["ok"]:
                times.append(r["elapsed_ms"])
            else:
                errs += 1
        p = percentiles(times)
        record("A.baseline " + method + " " + path, **p, errors=errs)


def scenario_b_concurrent_state(duration_s: int = 30, workers: int = 50):
    """Read-heavy: 50 threads slamming /api/state for 30s."""
    deadline = time.time() + duration_s
    times = []
    errors = 0
    completed = 0

    def hit():
        nonlocal errors, completed
        local = []
        while time.time() < deadline:
            r = _request("GET", "/api/state")
            if r["ok"]:
                local.append(r["elapsed_ms"])
            else:
                errors += 1
            completed += 1
        return local

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(hit) for _ in range(workers)]
        for f in as_completed(futures):
            times.extend(f.result())
    elapsed = time.time() - t0
    p = percentiles(times)
    record("B.state x50 conc / 30s", **p, errors=errors, total_requests=completed, rps=round(completed / elapsed, 1), duration_s=round(elapsed, 1))


def scenario_c_concurrent_chat(duration_s: int = 20, workers: int = 25):
    """25 threads hammering /api/chat with cheap regex-router intents."""
    deadline = time.time() + duration_s
    times = []
    errors = 0
    completed = 0
    messages = ["status", "list senders", "list campaigns", "help", "breakdown", "count contacts"]

    def hit(idx):
        nonlocal errors, completed
        local = []
        while time.time() < deadline:
            msg = messages[(idx + completed) % len(messages)]
            r = _request("POST", "/api/chat", body={"session_id": f"loadchat-{idx}", "message": msg})
            if r["ok"]:
                local.append(r["elapsed_ms"])
            else:
                errors += 1
            completed += 1
        return local

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(hit, i) for i in range(workers)]
        for f in as_completed(futures):
            times.extend(f.result())
    elapsed = time.time() - t0
    p = percentiles(times)
    record("C.chat x25 conc / 20s", **p, errors=errors, total_requests=completed, rps=round(completed / elapsed, 1), duration_s=round(elapsed, 1))


def scenario_d_large_upload(rows: int = 50_000):
    """Upload a single CSV with N rows and measure server-side parse + insert time."""
    reset_contacts()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "first_name", "last_name", "consent_status"])
    for i in range(rows):
        w.writerow([f"u{i:06d}@loadtest.example.com", f"First{i}", f"Last{i}", "opted_in"])
    blob = buf.getvalue().encode()
    size_mb = len(blob) / (1024 * 1024)
    t0 = time.perf_counter()
    r = upload_csv(blob, name=f"big-{rows}.csv")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    body = json.loads(r["body"].decode()) if r.get("body") else {}
    record(f"D.upload {rows} rows ({size_mb:.1f}MB)",
           ok=r["ok"], status=r["status"], elapsed_ms=round(elapsed_ms, 1),
           imported=body.get("imported"), updated=body.get("updated"),
           skipped=body.get("skipped"), throughput_rows_per_s=round(rows / (elapsed_ms / 1000), 1) if elapsed_ms else 0)


def scenario_e_state_at_scale():
    """Measure /api/state response time and size with the large contact list still loaded."""
    times = []
    sizes = []
    errors = 0
    for _ in range(100):
        r = _request("GET", "/api/state")
        if r["ok"]:
            times.append(r["elapsed_ms"])
            sizes.append(r["bytes"])
        else:
            errors += 1
    p = percentiles(times)
    record("E.state at-scale x100", **p, errors=errors,
           response_bytes_avg=round(statistics.mean(sizes) if sizes else 0),
           response_bytes_max=max(sizes) if sizes else 0)


def scenario_f_concurrent_uploads(workers: int = 20, per_worker_rows: int = 100):
    """20 simultaneous /api/import uploads — write contention test."""
    reset_contacts()
    def one(idx):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["email"])
        for i in range(per_worker_rows):
            w.writerow([f"w{idx}u{i:04d}@conc.example.com"])
        blob = buf.getvalue().encode()
        r = upload_csv(blob, name=f"conc-{idx}.csv")
        return r["ok"], r["elapsed_ms"]
    t0 = time.time()
    times = []; errs = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(one, i) for i in range(workers)]
        for f in as_completed(futures):
            ok, ms = f.result()
            if ok: times.append(ms)
            else: errs += 1
    elapsed = time.time() - t0
    # confirm correctness — how many contacts ended up in state?
    state = json.loads(_request("GET", "/api/state")["body"].decode())
    contacts_loaded = len(state.get("contacts", []))
    expected = workers * per_worker_rows
    p = percentiles(times)
    record(f"F.uploads x{workers} conc ({per_worker_rows} rows ea)",
           **p, errors=errs, total_uploads=workers, duration_s=round(elapsed, 1),
           contacts_in_state=contacts_loaded, expected=expected,
           correctness=("ok" if contacts_loaded >= expected else "DATA LOSS"))


def scenario_g_ramp_concurrency():
    """Find the failure cliff. Ramp 10 → 500 concurrent /api/state requests; record errors at each level."""
    for n in (10, 50, 100, 200, 300, 500):
        times = []; errs = 0
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=n) as ex:
            futures = [ex.submit(_request, "GET", "/api/state") for _ in range(n)]
            for f in as_completed(futures):
                r = f.result()
                if r["ok"]: times.append(r["elapsed_ms"])
                else: errs += 1
        elapsed = time.time() - t0
        p = percentiles(times)
        record(f"G.ramp conc={n}", **p, errors=errs, completed=len(times), duration_s=round(elapsed, 2))
        if errs > n // 5:  # 20% error rate — back off
            record(f"G.ramp halted at conc={n} (>20% errors)", errors=errs)
            break


def scenario_h_bulk_send_dryrun(recipients: int = 200):
    """Launch a dry-run bulk campaign with N recipients and time end-to-end completion."""
    sid = ensure_dryrun_active()
    # Seed recipients
    reset_contacts()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "consent_status"])
    for i in range(recipients):
        w.writerow([f"bulk{i:04d}@dryrun.example.com", "opted_in"])
    upload_csv(buf.getvalue().encode(), name="bulk.csv")

    # Create campaign with delay=0.05 (fast for testing) via /api/campaigns
    create = _request("POST", "/api/campaigns", body={
        "sender_id": sid, "name": f"LoadBulk-{recipients}", "campaign_type": "transactional",
        "subject": "load probe", "purpose": "load-suite",
        "html_body": "<p>Hi {{first_name}}, load probe.</p><p>{{unsubscribe_url}} · {{physical_address}}</p>",
        "plain_body": "Hi {{first_name}}, load probe. {{unsubscribe_url}} {{physical_address}}",
        "delay_seconds": 0.05,
    })
    body = json.loads(create["body"].decode())
    cid = body["campaign"]["id"]
    eligible = body["validation"]["eligible_count"]
    if not body["validation"]["can_send"]:
        record(f"H.bulk skipped (can_send=false)", failing=[c["key"] for c in body["validation"]["checks"] if not c["ok"]])
        return

    t0 = time.perf_counter()
    send = _request("POST", f"/api/campaigns/{cid}/send", body={})
    if not send["ok"]:
        record(f"H.bulk start failed", status=send["status"])
        return

    # Poll until done
    deadline = t0 + 600
    sent = failed = queued = 0
    while time.perf_counter() < deadline:
        state = json.loads(_request("GET", "/api/state")["body"].decode())
        camp = next((c for c in state["campaigns"] if c["id"] == cid), None)
        if not camp:
            break
        recip = camp.get("recipients", [])
        sent = sum(1 for r in recip if r.get("status") == "sent")
        failed = sum(1 for r in recip if r.get("status") == "failed")
        queued = sum(1 for r in recip if r.get("status") == "queued")
        if camp.get("status") in ("sent", "completed_with_failures") and queued == 0:
            break
        time.sleep(0.5)
    elapsed = time.perf_counter() - t0
    rate = (sent + failed) / elapsed if elapsed else 0
    record(f"H.bulk dryrun x{recipients}",
           elapsed_s=round(elapsed, 2), sent=sent, failed=failed, queued_remaining=queued,
           rate_per_s=round(rate, 1), eligible=eligible)


def scenario_i_error_flood(count: int = 500):
    """Hammer /api/chat with garbage; the server must stay up and respond quickly."""
    payloads = [
        b'{"session_id":',                                         # invalid JSON
        json.dumps({"message": "x" * 50_000}).encode(),            # huge message
        json.dumps({"session_id": "", "message": ""}).encode(),    # empty
        b"not json at all",                                        # not JSON
    ]
    times = []
    errs_500 = 0
    by_status: dict = {}
    for i in range(count):
        body = payloads[i % len(payloads)]
        r = _request("POST", "/api/chat", body=body, headers={"Content-Type": "application/json"})
        times.append(r["elapsed_ms"])
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        if r["status"] >= 500:
            errs_500 += 1
    p = percentiles(times)
    record(f"I.bad-input flood x{count}", **p, by_status=by_status, server_5xx=errs_500)

    # After the flood, server must still answer a normal request quickly
    r = _request("GET", "/api/state")
    record("I.post-flood health check", ok=r["ok"], status=r["status"], elapsed_ms=round(r["elapsed_ms"], 1))


def scenario_j_memory_growth():
    """Final state size on disk + in-memory state.json bytes (proxy for memory growth)."""
    state = json.loads(_request("GET", "/api/state")["body"].decode())
    record("J.final state",
           contacts=len(state.get("contacts", [])),
           campaigns=len(state.get("campaigns", [])),
           events=len(state.get("events", [])),
           audit_log=len(state.get("audit_log", [])),
           senders=len(state.get("senders", [])),
           suppression=len(state.get("suppression", [])),
           state_response_bytes=len(_request("GET", "/api/state")["body"]))


# ---------------- Runner ----------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Short version (CI)")
    parser.add_argument("--skip-large", action="store_true", help="Skip the 50k-row upload")
    args = parser.parse_args()

    print(f"=== OmniAI load/stress suite against {BASE} ===")
    print(f"started at {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    overall_t0 = time.time()

    ensure_dryrun_active()

    scenario_a_baseline()
    scenario_b_concurrent_state(duration_s=10 if args.quick else 30)
    scenario_c_concurrent_chat(duration_s=10 if args.quick else 20)
    if not args.skip_large:
        scenario_d_large_upload(rows=10_000 if args.quick else 50_000)
    scenario_e_state_at_scale()
    scenario_f_concurrent_uploads(workers=10 if args.quick else 20, per_worker_rows=50 if args.quick else 100)
    scenario_g_ramp_concurrency()
    scenario_h_bulk_send_dryrun(recipients=100 if args.quick else 200)
    scenario_i_error_flood(count=200 if args.quick else 500)
    scenario_j_memory_growth()

    RESULTS["total_duration_s"] = round(time.time() - overall_t0, 1)
    RESULTS["base"] = BASE
    RESULTS["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out = "load_stress_results.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, indent=2, default=str)
    print(f"\nDone in {RESULTS['total_duration_s']}s — results: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
