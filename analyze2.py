#!/usr/bin/env python3
"""
Revised log analysis - focusing on POST /checkout failures.
"""

import re
from collections import defaultdict
from datetime import datetime

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
with open(WEB_LOG) as f:
    for line in f:
        m = web_pattern.search(line)
        if m:
            web_entries.append(m.groupdict())

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

worker_completed = {}
worker_failed    = {}  # request_id -> {ts, err}

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
                "err": m.group("err_msg").strip(),
                "raw": line.strip(),
            }

print(f"Total web entries: {len(web_entries)}")
print(f"Worker completed:  {len(worker_completed)}")
print(f"Worker failed:     {len(worker_failed)}")

# ─── Focus: POST /checkout ────────────────────────────────────────────────────
checkout = [e for e in web_entries if e["path"] == "/checkout" and e["method"] == "POST"]
checkout_ids = {e["request_id"] for e in checkout}
c_completed = {rid for rid in checkout_ids if rid in worker_completed}
c_failed    = {rid for rid in checkout_ids if rid in worker_failed}
c_missing   = checkout_ids - c_completed - c_failed

print(f"\n=== POST /checkout ===")
print(f"  Total requests:    {len(checkout)}")
print(f"  Worker completed:  {len(c_completed)}")
print(f"  Worker FAILED:     {len(c_failed)}")
print(f"  Not seen in worker:{len(c_missing)}")

# Build enriched failed checkout list
failed_entries = []
for e in checkout:
    if e["request_id"] in c_failed:
        w = worker_failed[e["request_id"]]
        failed_entries.append({**e, "worker_ts": w["ts"], "worker_err": w["err"]})

failed_entries.sort(key=lambda x: x["ts"])

# ─── Q1: When did the problem start? ─────────────────────────────────────────
print(f"\n=== Q1: When did the problem start? ===")
first_fail = failed_entries[0]
print(f"  First failed checkout:")
print(f"    web ts:      {first_fail['ts']}")
print(f"    worker ts:   {first_fail['worker_ts']}")
print(f"    request_id:  {first_fail['request_id']}")
print(f"    user_id:     {first_fail['user_id']}")
print(f"    worker_err:  {first_fail['worker_err']}")

# Last successful checkout before first failure
ok_entries = [e for e in checkout if e["request_id"] in c_completed]
ok_entries.sort(key=lambda x: x["ts"])
last_ok_before = [e for e in ok_entries if e["ts"] < first_fail["ts"]]
if last_ok_before:
    print(f"\n  Last SUCCESSFUL checkout before first failure:")
    lo = last_ok_before[-1]
    print(f"    web ts:      {lo['ts']}")
    print(f"    request_id:  {lo['request_id']}")

# ─── Q3: What do failing requests have in common? ────────────────────────────
print(f"\n=== Q3: Pattern analysis ===")

# Error messages
err_msgs = defaultdict(int)
for fe in failed_entries:
    err_msgs[fe["worker_err"]] += 1
print(f"  Worker error messages:")
for msg, cnt in sorted(err_msgs.items(), key=lambda x: -x[1]):
    print(f"    [{cnt:4d}x]  {msg}")

# Upstream IPs
upstream_re = re.compile(r"upstream=(\S+)")
upstream_counts = defaultdict(int)
for fe in failed_entries:
    m = upstream_re.search(fe["worker_err"])
    if m:
        upstream_counts[m.group(1)] += 1
print(f"\n  Upstreams involved:")
for ip, cnt in sorted(upstream_counts.items(), key=lambda x: -x[1]):
    print(f"    {ip}: {cnt} failures")

# Status codes
statuses = defaultdict(int)
for fe in failed_entries:
    statuses[fe["status"]] += 1
print(f"\n  HTTP status codes of failing web requests: {dict(statuses)}")

# Latency
fail_lat = [int(e["latency"]) for e in failed_entries]
ok_lat   = [int(e["latency"]) for e in ok_entries]
print(f"\n  Latency (ms):")
print(f"    Failed:    avg={sum(fail_lat)/len(fail_lat):.1f}, min={min(fail_lat)}, max={max(fail_lat)}")
print(f"    Completed: avg={sum(ok_lat)/len(ok_lat):.1f}, min={min(ok_lat)}, max={max(ok_lat)}")

# ─── Hourly breakdown ─────────────────────────────────────────────────────────
hourly_fail = defaultdict(int)
hourly_ok   = defaultdict(int)
for e in checkout:
    h = e["ts"][11:13]
    if e["request_id"] in c_failed:
        hourly_fail[h] += 1
    elif e["request_id"] in c_completed:
        hourly_ok[h] += 1

print(f"\n  Hourly breakdown (hour: ok | failed | fail%):")
for h in sorted(set(list(hourly_fail.keys()) + list(hourly_ok.keys()))):
    total = hourly_ok[h] + hourly_fail[h]
    pct = 100 * hourly_fail[h] / total if total else 0
    marker = " <-- FAIL STARTS" if h == first_fail["ts"][11:13] and hourly_ok[h] > 0 else ""
    marker = " <<<<" if hourly_fail[h] > 0 and hourly_ok[h] == 0 else marker
    print(f"    {h}:00 -> ok={hourly_ok[h]:4d}, failed={hourly_fail[h]:4d}  ({pct:5.1f}%){marker}")

# ─── Q4: Distinct users affected ─────────────────────────────────────────────
affected_users = {e["user_id"] for e in failed_entries}
print(f"\n=== Q4: Distinct users affected: {len(affected_users)} ===")

# Also look at unique users who attempted checkout vs those who failed
all_checkout_users = {e["user_id"] for e in checkout}
ok_checkout_users  = {e["user_id"] for e in ok_entries}
print(f"  Total unique users who attempted checkout:   {len(all_checkout_users)}")
print(f"  Unique users with successful checkout:       {len(ok_checkout_users)}")
print(f"  Unique users with at least one failed:       {len(affected_users)}")
# Users who ONLY failed (never succeeded)
only_failed = affected_users - ok_checkout_users
print(f"  Users who ONLY had failures (no success):    {len(only_failed)}")

# ─── Bonus: Root cause investigation ─────────────────────────────────────────
print(f"\n=== Bonus: Root cause clues ===")

# All distinct upstreams in the entire worker failed set
all_upstream_counts = defaultdict(int)
all_err_type_counts = defaultdict(int)
with open(WORKER_LOG) as f:
    for line in f:
        if "ERROR [worker]" in line:
            m = upstream_re.search(line)
            if m:
                all_upstream_counts[m.group(1)] += 1
            # err type (first word after "upstream call failed" etc)
            if "ECONNRESET" in line:
                all_err_type_counts["ECONNRESET"] += 1
            elif "ETIMEDOUT" in line:
                all_err_type_counts["ETIMEDOUT"] += 1
            elif "ECONNREFUSED" in line:
                all_err_type_counts["ECONNREFUSED"] += 1
            else:
                all_err_type_counts["other"] += 1

print(f"  All upstream IPs with errors (worker.log):")
for ip, cnt in sorted(all_upstream_counts.items(), key=lambda x: -x[1]):
    print(f"    {ip}: {cnt}")

print(f"  Error type breakdown:")
for et, cnt in sorted(all_err_type_counts.items(), key=lambda x: -x[1]):
    print(f"    {et}: {cnt}")

# When did the upstream start failing?
upstream_err_times = []
with open(WORKER_LOG) as f:
    for line in f:
        if "ERROR [worker]" in line and "10.0.3.44:8443" in line:
            m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)", line)
            if m:
                upstream_err_times.append(m.group(1))

upstream_err_times.sort()
print(f"\n  First error mentioning upstream 10.0.3.44:8443: {upstream_err_times[0] if upstream_err_times else 'none'}")
print(f"  Last error mentioning upstream 10.0.3.44:8443:  {upstream_err_times[-1] if upstream_err_times else 'none'}")
print(f"  Total errors against 10.0.3.44:8443: {len(upstream_err_times)}")

# Does the failure rate ever drop? (recovery check)
print(f"\n  Minute-by-minute: first 20 minutes with failures")
minute_ok   = defaultdict(int)
minute_fail = defaultdict(int)
for e in checkout:
    minute = e["ts"][:16]
    if e["request_id"] in c_failed:
        minute_fail[minute] += 1
    elif e["request_id"] in c_completed:
        minute_ok[minute] += 1

fail_minutes = sorted(minute_fail.keys())
for mm in fail_minutes[:20]:
    total = minute_ok[mm] + minute_fail[mm]
    print(f"    {mm}  ok={minute_ok[mm]:2d}  failed={minute_fail[mm]:2d}  total={total}")

# Show full sample of first failure log lines
print(f"\n  First 5 failing checkout web entries:")
for fe in failed_entries[:5]:
    print(f"    {fe['ts']} user={fe['user_id']} rid={fe['request_id']} status={fe['status']} lat={fe['latency']}ms")
    print(f"      worker_err: {fe['worker_err']}")

print("\nDone.")
