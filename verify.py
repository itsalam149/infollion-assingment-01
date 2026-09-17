#!/usr/bin/env python3
"""
Deep verification script - validates every claim in ANSWERS.md.
"""

import re
from collections import defaultdict

WEB_LOG    = "/Users/faqrealam149/Downloads/assingment1/web.log"
WORKER_LOG = "/Users/faqrealam149/Downloads/assingment1/worker.log"

# ─── Parse web.log ────────────────────────────────────────────────────────────
web_pattern = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) "
    r"(?P<level>\w+) \[request\] "
    r"method=(?P<method>\w+) "
    r"path=(?P<path>\S+) "
    r"status=(?P<status>\d+) "
    r"latency_ms=(?P<latency>\d+) "
    r"user_id=(?P<user_id>\d+) "
    r"request_id=(?P<request_id>\S+)"
)

web_entries = []
unparsed_web = 0
with open(WEB_LOG) as f:
    for line in f:
        line = line.rstrip()
        if not line.strip():
            continue
        m = web_pattern.search(line)
        if m:
            web_entries.append({**m.groupdict(), "raw": line})
        else:
            unparsed_web += 1

print(f"[WEB] Parsed: {len(web_entries)} | Unparsed lines: {unparsed_web}")

# ─── Parse worker.log RAW (capture the FULL line) ────────────────────────────
# Critical: parse entire line so we don't miss fields
worker_completed = {}  # request_id -> ts
worker_failed    = {}  # request_id -> full parsed info

wok_pat = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) "
    r"INFO \[worker\] job completed request_id=(?P<request_id>\S+) duration_ms=(?P<dur>\d+)"
)
werr_pat = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) "
    r"ERROR \[worker\] (?P<msg>.+)"  # capture everything after ERROR [worker]
)

unparsed_worker = 0
metrics_errors  = 0
other_lines     = []

with open(WORKER_LOG) as f:
    for line in f:
        line = line.rstrip()
        if not line.strip():
            continue

        # metrics-worker noise
        if "[metrics-worker]" in line:
            metrics_errors += 1
            continue

        m = wok_pat.search(line)
        if m:
            worker_completed[m.group("request_id")] = {
                "ts": m.group("ts"),
                "duration_ms": int(m.group("dur")),
            }
            continue

        m = werr_pat.search(line)
        if m:
            rest = m.group("msg")
            # now parse request_id, err, upstream out of rest
            rid_m = re.search(r"request_id=(\S+)", rest)
            err_m = re.search(r"err=(\S+)", rest)
            ups_m = re.search(r"upstream=(\S+)", rest)
            if rid_m:
                worker_failed[rid_m.group(1)] = {
                    "ts"       : m.group("ts"),
                    "msg"      : rest,
                    "err"      : err_m.group(1) if err_m else None,
                    "upstream" : ups_m.group(1) if ups_m else None,
                    "raw"      : line,
                }
            else:
                other_lines.append(line)
            continue

        unparsed_worker += 1
        other_lines.append(line)

print(f"[WORKER] Completed: {len(worker_completed)} | Failed: {len(worker_failed)}")
print(f"[WORKER] Metrics noise: {metrics_errors} | Unparsed/other: {unparsed_worker}")
if other_lines:
    print(f"  Sample unparsed/other worker lines:")
    for l in other_lines[:5]:
        print(f"    {l}")

# ─── CHECK 1: All unique paths / methods ─────────────────────────────────────
print("\n" + "="*60)
print("CHECK 1: All (method, path_prefix) combos in web.log")
path_methods = defaultdict(int)
for e in web_entries:
    # Normalise dynamic segments like /product/12345 -> /product/:id
    path = re.sub(r"/\d+$", "/:id", e["path"])
    path_methods[(e["method"], path)] += 1
for k, v in sorted(path_methods.items(), key=lambda x: -x[1]):
    print(f"  {k[0]:6s} {k[1]:<35s} {v:6d}")

# ─── CHECK 2: Cross-ref ALL endpoints vs worker ───────────────────────────────
print("\n" + "="*60)
print("CHECK 2: Every endpoint cross-referenced with worker outcomes")
endpoints = defaultdict(lambda: {"total":0, "completed":0, "failed":0, "missing":0})
for e in web_entries:
    path = re.sub(r"/\d+$", "/:id", e["path"])
    key  = f"{e['method']} {path}"
    endpoints[key]["total"] += 1
    rid = e["request_id"]
    if rid in worker_completed:
        endpoints[key]["completed"] += 1
    elif rid in worker_failed:
        endpoints[key]["failed"] += 1
    else:
        endpoints[key]["missing"] += 1

for k, v in sorted(endpoints.items(), key=lambda x: -x[1]["failed"]):
    if v["failed"] > 0 or v["completed"] > 0:
        pct = 100*v["failed"]/v["total"] if v["total"] else 0
        print(f"  {k:<35s} total={v['total']:5d} ok={v['completed']:5d} fail={v['failed']:5d} missing={v['missing']:5d} ({pct:.1f}%)")

# ─── CHECK 3: Are ALL worker errors really ECONNRESET to 10.0.3.44:8443? ─────
print("\n" + "="*60)
print("CHECK 3: Worker error breakdown (err field)")
err_types    = defaultdict(int)
upstream_ips = defaultdict(int)
has_retries_exhausted = 0
no_retries_msg = 0

for rid, info in worker_failed.items():
    err_types[info["err"] or "NONE"] += 1
    upstream_ips[info["upstream"] or "NONE"] += 1
    if "retries exhausted" in info["raw"]:
        has_retries_exhausted += 1
    else:
        no_retries_msg += 1

print(f"  Error types (err= field):")
for k, v in sorted(err_types.items(), key=lambda x: -x[1]):
    print(f"    {k}: {v}")
print(f"  Upstream IPs:")
for k, v in sorted(upstream_ips.items(), key=lambda x: -x[1]):
    print(f"    {k}: {v}")
print(f"  Lines with '(retries exhausted)': {has_retries_exhausted}")
print(f"  Lines WITHOUT 'retries exhausted': {no_retries_msg}")

# ─── CHECK 4: Are all failed worker jobs /checkout? ──────────────────────────
print("\n" + "="*60)
print("CHECK 4: What web endpoints do the failed worker jobs come from?")
failed_rids = set(worker_failed.keys())
web_by_rid  = {e["request_id"]: e for e in web_entries}
endpoint_for_fail = defaultdict(int)
no_web_entry = 0
for rid in failed_rids:
    if rid in web_by_rid:
        e = web_by_rid[rid]
        path = re.sub(r"/\d+$", "/:id", e["path"])
        endpoint_for_fail[f"{e['method']} {path}"] += 1
    else:
        no_web_entry += 1
for k, v in sorted(endpoint_for_fail.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")
print(f"  Worker failures with NO matching web entry: {no_web_entry}")

# ─── CHECK 5: Any checkout requests NOT in worker at all? ────────────────────
print("\n" + "="*60)
print("CHECK 5: POST /checkout requests not seen in worker at all")
checkout = [e for e in web_entries if e["path"] == "/checkout" and e["method"] == "POST"]
truly_missing = [e for e in checkout
                 if e["request_id"] not in worker_completed
                 and e["request_id"] not in worker_failed]
print(f"  Total POST /checkout:   {len(checkout)}")
print(f"  In worker_completed:    {sum(1 for e in checkout if e['request_id'] in worker_completed)}")
print(f"  In worker_failed:       {sum(1 for e in checkout if e['request_id'] in worker_failed)}")
print(f"  In NEITHER (truly lost):{len(truly_missing)}")
if truly_missing:
    for e in truly_missing[:5]:
        print(f"    {e['ts']} user={e['user_id']} rid={e['request_id']}")

# ─── CHECK 6: Verify the "first failure" timestamp precisely ─────────────────
print("\n" + "="*60)
print("CHECK 6: First failure - verify precisely")
failed_checkout = [e for e in checkout if e["request_id"] in worker_failed]
failed_checkout.sort(key=lambda x: x["ts"])

print(f"  Total failed checkouts: {len(failed_checkout)}")
print(f"  First 3:")
for e in failed_checkout[:3]:
    w = worker_failed[e["request_id"]]
    print(f"    web={e['ts']} worker={w['ts']} rid={e['request_id']} user={e['user_id']}")
    print(f"    err: {w['raw']}")

# Verify we haven't missed an earlier failure by checking ALL worker errors chronologically
all_errors_sorted = sorted(worker_failed.values(), key=lambda x: x["ts"])
print(f"\n  First 3 worker errors (any type) chronologically:")
for w in all_errors_sorted[:3]:
    print(f"    {w['ts']}  {w['raw']}")

# ─── CHECK 7: Does failure rate actually stabilise? Or recover? ───────────────
print("\n" + "="*60)
print("CHECK 7: Per-hour failure rate stability and recovery check")
hourly = defaultdict(lambda: {"ok": 0, "fail": 0})
for e in checkout:
    h = e["ts"][11:13]
    if e["request_id"] in worker_failed:
        hourly[h]["fail"] += 1
    elif e["request_id"] in worker_completed:
        hourly[h]["ok"]   += 1

for h in sorted(hourly.keys()):
    ok, fail = hourly[h]["ok"], hourly[h]["fail"]
    total = ok + fail
    pct = 100*fail/total if total else 0
    print(f"  {h}:00  ok={ok:4d}  fail={fail:4d}  total={total:4d}  fail%={pct:5.1f}%")

# ─── CHECK 8: Distinct user counts - verify carefully ────────────────────────
print("\n" + "="*60)
print("CHECK 8: User impact - detailed verification")
all_users_checkout    = {e["user_id"] for e in checkout}
users_with_any_fail   = {e["user_id"] for e in checkout if e["request_id"] in worker_failed}
users_with_any_ok     = {e["user_id"] for e in checkout if e["request_id"] in worker_completed}
users_only_fail       = users_with_any_fail - users_with_any_ok
users_mixed           = users_with_any_fail & users_with_any_ok

print(f"  Unique users who attempted checkout:   {len(all_users_checkout)}")
print(f"  Users with >= 1 failed checkout:       {len(users_with_any_fail)}")
print(f"  Users with >= 1 successful checkout:   {len(users_with_any_ok)}")
print(f"  Users with ONLY failures (no success): {len(users_only_fail)}")
print(f"  Users with MIXED (some ok, some fail): {len(users_mixed)}")

# Do any users have failures BEFORE 14:32? (i.e. did we truly start correctly?)
pre_incident_fails = [e for e in failed_checkout if e["ts"] < "2026-07-02 14:32:40"]
print(f"\n  Failed checkouts BEFORE 14:32:40:      {len(pre_incident_fails)}")
if pre_incident_fails:
    for e in pre_incident_fails:
        print(f"    {e['ts']} rid={e['request_id']}")

# ─── CHECK 9: Verify the "last successful checkout" before first failure ──────
print("\n" + "="*60)
print("CHECK 9: Last successful checkout before 14:32:40.073")
ok_checkout = [e for e in checkout if e["request_id"] in worker_completed]
ok_checkout.sort(key=lambda x: x["ts"])
before_fail = [e for e in ok_checkout if e["ts"] < "2026-07-02 14:32:40.073"]
if before_fail:
    lo = before_fail[-1]
    print(f"  Last ok before incident: {lo['ts']} rid={lo['request_id']} user={lo['user_id']}")
    # Verify in worker log too
    w = worker_completed.get(lo["request_id"])
    print(f"  Worker completed at: {w['ts'] if w else 'NOT FOUND'}")

# ─── CHECK 10: Are there any other upstream IPs in the entire worker.log? ─────
print("\n" + "="*60)
print("CHECK 10: ALL upstreams referenced in worker.log")
all_upstreams = defaultdict(int)
with open(WORKER_LOG) as f:
    for line in f:
        m = re.search(r"upstream=(\S+)", line)
        if m:
            all_upstreams[m.group(1)] += 1
for ip, cnt in sorted(all_upstreams.items(), key=lambda x: -x[1]):
    print(f"  {ip}: {cnt}")

# ─── CHECK 11: Status code distribution for ALL web.log entries ───────────────
print("\n" + "="*60)
print("CHECK 11: Status code distribution for ALL web requests")
all_statuses = defaultdict(int)
for e in web_entries:
    all_statuses[e["status"]] += 1
for s, c in sorted(all_statuses.items(), key=lambda x: -x[1]):
    print(f"  {s}: {c}")

# ─── CHECK 12: Any 5xx or 4xx on /checkout specifically? ─────────────────────
print("\n" + "="*60)
print("CHECK 12: /checkout status codes")
checkout_statuses = defaultdict(int)
for e in checkout:
    checkout_statuses[e["status"]] += 1
for s, c in sorted(checkout_statuses.items()):
    print(f"  {s}: {c}")

print("\n=== ALL CHECKS COMPLETE ===")
