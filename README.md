# 🔍 Infollion Log Reading Assignment

> **Production Incident Investigation** — Identifying why user orders silently vanished using only raw log files.

---

## 📋 Assignment Overview

Given two production log files captured over the same 24-hour window:

| File | Description |
|------|-------------|
| `web.log` | HTTP request logs — method, path, status, user_id, request_id |
| `worker.log` | Background job processor — job outcomes keyed by request_id |

**The Problem:** Users were successfully placing orders, but some orders never appeared in the system. No dashboards. No alerts. Just the logs.

---

## 🚨 Incident Summary

| | Detail |
|-|--------|
| **Incident Start** | `2026-07-02 14:32:40` |
| **Affected Endpoint** | `POST /checkout` |
| **Root Cause** | Upstream service at `10.0.3.44:8443` went down (`ECONNRESET`) |
| **Total Failures** | 2,385 checkout jobs silently failed |
| **Users Affected** | **2,335 distinct users** |
| **Recovery** | ❌ Never recovered within the log window (through 23:59) |

---

## ❓ Questions & Answers

### Q1 — When did the problem start?

**`2026-07-02 14:32:40.073`**

The first failing request hit the web server at this timestamp and returned `202 Accepted` — looking completely normal. Two seconds later, the worker job failed:

```
2026-07-02 14:32:42.692 ERROR [worker] upstream call failed
  request_id=16ce72300cf58a32
  err=ECONNRESET
  upstream=10.0.3.44:8443
  (retries exhausted)
```

The last *successful* checkout was just 9 seconds earlier at `14:32:31.237`.

---

### Q2 — Which endpoint is affected?

**`POST /checkout`**

| Endpoint | Total Requests | Worker OK | Worker FAILED | Fail % |
|----------|---------------|-----------|---------------|--------|
| `POST /checkout` | 9,875 | 7,490 | **2,385** | **24.2%** |
| `POST /orders` | 7,998 | 7,998 | 0 | 0% |
| `POST /login` | 10,044 | 10,044 | 0 | 0% |
| `POST /cart` | 12,037 | N/A | 0 | 0% |

Every single worker failure maps to a `/checkout` request — no other endpoint involved.

---

### Q3 — What do the failing requests have in common?

Every one of the 2,385 failures shares an **identical** worker error:

```
upstream call failed err=ECONNRESET upstream=10.0.3.44:8443 (retries exhausted)
```

- ✅ Same error type (`ECONNRESET`) — 2,385/2,385
- ✅ Same upstream (`10.0.3.44:8443`) — 2,385/2,385 — **only upstream in the entire log**
- ✅ All said `(retries exhausted)` — 2,385/2,385

**The web tier was completely blind to this.** All failing checkouts returned `HTTP 202` with normal latency (~71ms), so users thought their order succeeded.

**Failure rate over time:**

| Hour | OK | Failed | Fail % |
|------|----|--------|--------|
| 00:00 – 13:59 | 3,883 | 0 | 0.0% |
| 14:00 | 579 | 40 | **6.5%** ← incident starts |
| 15:00 | 384 | 267 | 41.0% |
| 16:00 | 384 | 303 | 44.1% |
| 17:00–23:59 | 2,211 | 1,706 | ~44% |

---

### Q4 — How many distinct users were affected?

**2,335 distinct users** had at least one failed checkout.

| Category | Count |
|----------|-------|
| Users with ≥ 1 failed checkout | **2,335** |
| Users with ONLY failures (zero orders got through) | 2,204 |
| Users with mixed results (some ok, some failed) | 131 |

---

### 🎯 Bonus — Root Cause

The internal service at **`10.0.3.44:8443`** crashed or went offline at ~14:32 and was **never restored** during the observation window.

- `ECONNRESET` means the host is **actively resetting TCP connections** — not a timeout, a hard reset. Consistent with a process crash or dropped firewall rule.
- 100% of all worker errors target this single IP — no other host in the logs.
- Failure rate climbed from 0% → 6.5% → ~44% in under 30 minutes and held there for 9+ hours.
- Because checkout is async (web accepts → hands off to worker), the web tier always returned `202` — making this invisible to users until they checked their orders.

---

## 🛠️ Investigation Process

- **Examined log structure** — understood that `request_id` is the join key between the two log files
- **Parsed and counted** all endpoints; initially focused on `/orders` (mentioned in the problem), found zero worker failures there
- **Pivoted to `/checkout`** after cross-referencing all request_ids against worker outcomes — found 2,385 failures
- **Ruled out unrelated noise:** 793 `AnalyticsUploadTimeout` errors from `metrics-worker` (different subsystem, no request_id), 192 product `404`s, 118 auth `401`s, 1,502 `WARN [db] slow query` lines — none related to incident
- **Pinpointed exact start timestamp** (`14:32:40.073`) by sorting all failures chronologically and verifying no earlier failures exist
- **Identified the upstream IP** (`10.0.3.44:8443`) as the single point of failure; confirmed it's the only upstream referenced in the entire worker log
- **Verified web-side invisibility** — all 9,875 checkout requests returned `202 Accepted` with normal latency; no 5xx errors
- **Checked for recovery** — hourly breakdown shows failure rate never dropped below 41% from 15:00 onwards through end of log
- **Quantified user impact** — 2,335 affected users, with 2,204 receiving zero successful orders

---

## 📁 Repository Structure

```
.
├── ANSWERS.md        # Full answers with evidence and investigation process
├── analyze.py        # First-pass analysis across all endpoints
├── analyze2.py       # Deep-dive analysis focused on /checkout failures
├── verify.py         # 12-point verification script validating every claim
└── README.md         # This file
```

> ⚠️ Log files (`web.log`, `worker.log`) are not included per assignment instructions.

---

## 🚀 Running the Analysis

```bash
# Run full analysis
python3 analyze2.py

# Run verification of all claims
python3 verify.py
```

**Requirements:** Python 3.6+ · No external dependencies (stdlib only)

---

*Submitted for Infollion Assignment — Delhi Technological University*
