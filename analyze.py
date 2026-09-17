#!/usr/bin/env python3
"""
Log analysis script for the production incident investigation.
"""

import re
from collections import defaultdict
from datetime import datetime

WEB_LOG = "/Users/faqrealam149/Downloads/assingment1/web.log"
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
with open(WEB_LOG) as f:
    for line in f:
        m = web_pattern.search(line)
        if m:
            web_entries.append(m.groupdict())

print(f"Total web log entries parsed: {len(web_entries)}")

# ─── Parse worker.log ─────────────────────────────────────────────────────────
worker_ok_pattern = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) "
    r"INFO \[worker\] job completed "
    r"request_id=(?P<request_id>\S+)"
)
worker_err_pattern = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+) "
    r"ERROR \[worker\] (?P<err_msg>.+?) "
    r"request_id=(?P<request_id>\S+)"
)

worker_completed = {}   # request_id -> ts
worker_failed    = {}   # request_id -> {ts, err_msg}

with open(WORKER_LOG) as f:
    for line in f:
        m = worker_ok_pattern.search(line)
        if m:
            worker_completed[m.group("request_id")] = m.group("ts")
            continue
        m = worker_err_pattern.search(line)
        if m:
            worker_failed[m.group("request_id")] = {
                "ts": m.group("ts"),
                "err": m.group("err_msg"),
            }

print(f"Worker jobs completed: {len(worker_completed)}")
print(f"Worker jobs FAILED:    {len(worker_failed)}")

# ─── Misc / unrelated worker errors (no request_id) ──────────────────────────
metrics_errors = 0
with open(WORKER_LOG) as f:
    for line in f:
        if "metrics-worker" in line and "ERROR" in line:
            metrics_errors += 1
print(f"Unrelated metrics-worker errors (AnalyticsUploadTimeout): {metrics_errors}")

# ─── Q2: Which endpoint is affected? ─────────────────────────────────────────
# Cross-reference: POST /orders requests vs worker outcomes
orders_requests = [e for e in web_entries if e["path"] == "/orders" and e["method"] == "POST"]
print(f"\n--- POST /orders ---")
print(f"  Total POST /orders requests: {len(orders_requests)}")
orders_ids = {e["request_id"] for e in orders_requests}
orders_completed = orders_ids & set(worker_completed)
orders_failed    = orders_ids & set(worker_failed)
orders_missing   = orders_ids - set(worker_completed) - set(worker_failed)
print(f"  Worker completed: {len(orders_completed)}")
print(f"  Worker FAILED:    {len(orders_failed)}")
print(f"  Not seen in worker at all: {len(orders_missing)}")

# Check other endpoints
for path in ["/checkout", "/cart", "/login"]:
    reqs = [e for e in web_entries if e["path"] == path]
    req_ids = {e["request_id"] for e in reqs}
    failed = req_ids & set(worker_failed)
    completed = req_ids & set(worker_completed)
    not_seen = req_ids - set(worker_completed) - set(worker_failed)
    print(f"\n--- POST {path} ---")
    print(f"  Total: {len(reqs)} | worker completed: {len(completed)} | failed: {len(failed)} | not in worker: {len(not_seen)}")

# ─── Q1: When did the problem start? ─────────────────────────────────────────
# Find the first failed order
failed_orders = [e for e in orders_requests if e["request_id"] in orders_failed]
failed_orders.sort(key=lambda e: e["ts"])
if failed_orders:
    first_fail = failed_orders[0]
    print(f"\n--- First failing order ---")
    print(f"  Timestamp:  {first_fail['ts']}")
    print(f"  user_id:    {first_fail['user_id']}")
    print(f"  request_id: {first_fail['request_id']}")
    worker_info = worker_failed.get(first_fail["request_id"], {})
    print(f"  Worker err: {worker_info}")

# Also: first worker error of any kind
all_worker_errors = {}
with open(WORKER_LOG) as f:
    for line in f:
        m = worker_err_pattern.search(line)
        if m:
            all_worker_errors[m.group("request_id")] = {"ts": m.group("ts"), "err": m.group("err_msg"), "line": line.strip()}

all_worker_error_list = sorted(all_worker_errors.values(), key=lambda x: x["ts"])
if all_worker_error_list:
    print(f"\n--- Very first worker ERROR of any kind ---")
    print(f"  {all_worker_error_list[0]['line']}")

# ─── Q3: What do failing requests have in common? ────────────────────────────
print(f"\n--- Pattern analysis of failed orders ---")
# Status codes of failing orders
statuses = defaultdict(int)
for e in orders_requests:
    if e["request_id"] in orders_failed:
        statuses[e["status"]] += 1
print(f"  Status codes of failed orders: {dict(statuses)}")

# Latency comparison
failed_latencies = [int(e["latency"]) for e in orders_requests if e["request_id"] in orders_failed]
ok_latencies = [int(e["latency"]) for e in orders_requests if e["request_id"] in orders_completed]
if failed_latencies:
    print(f"  Avg latency (failed):    {sum(failed_latencies)/len(failed_latencies):.1f} ms")
if ok_latencies:
    print(f"  Avg latency (completed): {sum(ok_latencies)/len(ok_latencies):.1f} ms")

# Error messages in worker for failed orders
err_msgs = defaultdict(int)
for rid in orders_failed:
    info = worker_failed.get(rid, {})
    err_msgs[info.get("err", "unknown")] += 1
print(f"  Worker error types: {dict(err_msgs)}")

# Time pattern: when do failures occur?
hourly_fail = defaultdict(int)
hourly_ok   = defaultdict(int)
for e in orders_requests:
    hour = e["ts"][11:13]
    if e["request_id"] in orders_failed:
        hourly_fail[hour] += 1
    elif e["request_id"] in orders_completed:
        hourly_ok[hour] += 1

print(f"\n  Hourly breakdown (hour: ok / failed):")
for h in sorted(set(list(hourly_fail.keys()) + list(hourly_ok.keys()))):
    print(f"    {h}:00 -> ok={hourly_ok[h]:4d}, failed={hourly_fail[h]:4d}")

# ─── Q4: How many distinct users affected? ───────────────────────────────────
affected_users = {e["user_id"] for e in orders_requests if e["request_id"] in orders_failed}
print(f"\n--- Distinct users affected: {len(affected_users)} ---")

# ─── Bonus: Root cause clues ─────────────────────────────────────────────────
print(f"\n--- Bonus: Sample failing worker error lines ---")
count = 0
for rid in sorted(orders_failed)[:5]:
    info = worker_failed[rid]
    print(f"  request_id={rid} err={info['err']}")
    count += 1

# Show unique error patterns in worker for affected orders
unique_errs = set()
for rid in orders_failed:
    info = worker_failed.get(rid, {})
    unique_errs.add(info.get("err", ""))
print(f"\n  Unique worker error messages for failed orders:")
for e in unique_errs:
    print(f"    {e}")

# ─── Are there upstream IPs in errors? ───────────────────────────────────────
upstream_pattern = re.compile(r"upstream=(\S+)")
upstreams = defaultdict(int)
for rid in orders_failed:
    info = worker_failed.get(rid, {})
    m = upstream_pattern.search(info.get("err", ""))
    if m:
        upstreams[m.group(1)] += 1
print(f"\n  Upstreams referenced in failed order errors: {dict(upstreams)}")

# ─── Timeline: first healthy order, then first failure ───────────────────────
ok_orders = [e for e in orders_requests if e["request_id"] in orders_completed]
ok_orders.sort(key=lambda e: e["ts"])
failed_orders_sorted = sorted(failed_orders, key=lambda e: e["ts"])
print(f"\n--- Timeline ---")
if ok_orders:
    print(f"  First successful order: {ok_orders[0]['ts']} (request_id={ok_orders[0]['request_id']})")
if failed_orders_sorted:
    print(f"  First failed order:     {failed_orders_sorted[0]['ts']} (request_id={failed_orders_sorted[0]['request_id']})")

# ─── Bonus: failure rate over time (to find inflection point) ────────────────
minute_ok   = defaultdict(int)
minute_fail = defaultdict(int)
for e in orders_requests:
    minute = e["ts"][:16]   # "YYYY-MM-DD HH:MM"
    if e["request_id"] in orders_failed:
        minute_fail[minute] += 1
    elif e["request_id"] in orders_completed:
        minute_ok[minute] += 1

# Print minutes where failures first appeared
fail_minutes = sorted(minute_fail.keys())
print(f"\n  First 10 minutes with failed orders:")
for m in fail_minutes[:10]:
    print(f"    {m} -> ok={minute_ok[m]}, failed={minute_fail[m]}")

# ─── Does the problem ever recover? ──────────────────────────────────────────
last_ok_order = ok_orders[-1] if ok_orders else None
last_fail_order = failed_orders_sorted[-1] if failed_orders_sorted else None
print(f"\n  Last successful order: {last_ok_order['ts'] if last_ok_order else 'N/A'}")
print(f"  Last failed order:     {last_fail_order['ts'] if last_fail_order else 'N/A'}")

# Any orders after the first failure that succeeded?
first_fail_ts = failed_orders_sorted[0]["ts"] if failed_orders_sorted else None
ok_after_fail = [e for e in ok_orders if e["ts"] > first_fail_ts] if first_fail_ts else []
print(f"  Successful orders AFTER the first failure: {len(ok_after_fail)}")

print("\nDone.")
