# Log Reading Assignment – ANSWERS.md

## Q1 — When did the problem start?

**Timestamp:** `2026-07-02 14:32:40.073`

**Evidence:**

The first failing checkout request appears at `14:32:40` in `web.log`:

```
2026-07-02 14:32:40.073 INFO [request] method=POST path=/checkout status=202 latency_ms=36 user_id=59787 request_id=16ce72300cf58a32
```

Two seconds later, the corresponding worker job fails:

```
2026-07-02 14:32:42.692 ERROR [worker] upstream call failed request_id=16ce72300cf58a32 err=ECONNRESET upstream=10.0.3.44:8443 (retries exhausted)
```

The last **successful** checkout before this was at `14:32:31.237`, meaning the outage
began in the ~9-second window between `14:32:31` and `14:32:40`. The failure rate
escalated rapidly from ~6.5% in the 14:00 hour to ~43-45% for every subsequent hour.

---

## Q2 — Which endpoint is affected?

**Endpoint:** `POST /checkout`

| Endpoint        | Total requests | Worker completed | Worker FAILED | Fail % |
|-----------------|---------------|-----------------|---------------|--------|
| `POST /checkout`| 9,875         | 7,490           | **2,385**     | **24.2%** |
| `POST /orders`  | 7,998         | 7,998           | 0             | 0% |
| `POST /login`   | 10,044        | 10,044          | 0             | 0% |
| `POST /cart`    | 12,037        | N/A (no async job) | 0          | 0% |

Every single worker failure is associated with a `POST /checkout` request. No other
endpoint is affected.

---

## Q3 — What do the failing requests have in common?

**All 2,385 failures share the same single pattern:**

- **Worker error:** `upstream call failed err=ECONNRESET upstream=10.0.3.44:8443 (retries exhausted)`
- **Error type:** `ECONNRESET` - the TCP connection was reset by the remote peer.
- **Upstream target:** A single internal service at **`10.0.3.44:8443`**.
- All failures note `(retries exhausted)`, meaning the worker retried and still could not reach the upstream.

**On the web side**, failing requests look completely normal:
- HTTP status: `202` (same as successful checkouts - the web server accepted the request fine)
- Latency: average `71.5 ms` (vs. `71.0 ms` for successful checkouts - indistinguishable)

This means the **web tier did not know about the failure** - it returned `202 Accepted`
and handed off to the background worker, which is why users "successfully placed orders"
but orders never appeared.

**Temporal pattern:**

| Hour | Successful | Failed | Fail % |
|------|-----------|--------|--------|
| 00-13 | 3,883 | 0 | 0% |
| 14 | 579 | 40 | 6.5% (problem starts) |
| 15 | 384 | 267 | 41.0% |
| 16 | 384 | 303 | 44.1% |
| 17 | 424 | 341 | 44.6% |
| 18 | 474 | 364 | 43.4% |
| 19-23 | 1,362 | 1,070 | ~44% |

The failure rate stabilises at roughly **43-45%** from 15:00 onwards and **never recovers** -
the upstream at `10.0.3.44:8443` remained partially or fully unavailable for the rest of the day.

---

## Q4 — How many distinct users were affected?

**2,335 distinct users** had at least one failed checkout.

- Of those, **2,204 users had no successful checkout at all** during the incident window.
- The remaining 131 had mixed results (some orders went through, others did not).

---

## Bonus — Root cause hypothesis

All evidence points to a single upstream internal service at **`10.0.3.44:8443`** becoming
unavailable starting at `14:32:42`:

- **100% of worker errors (2,385/2,385) reference `10.0.3.44:8443`** - no other host appears.
- The error is **`ECONNRESET`** (not `ETIMEDOUT`), indicating the remote host is
  actively resetting TCP connections rather than silently dropping them. This is consistent
  with a process crash, a misconfigured firewall rule, or a service restart that failed.
- The pattern "retries exhausted" confirms the worker retried multiple times and all
  attempts failed, ruling out a transient blip.
- The failure rate jumps from 0% to ~44% within ~30 minutes and **never recovers**,
  strongly suggesting the upstream service or its underlying infrastructure went down and
  was not restarted during the observation window.
- The web tier is **unaware** - it returns `202 Accepted` because the async handoff
  to the worker queue succeeds. Only the worker-to-upstream call fails.

**Likely root cause:** The internal service running at `10.0.3.44:8443` (likely the order
persistence service, inventory service, or payment processor) crashed or was taken
offline around `14:32`, and was never restored during the log capture window
(through `23:59`).

---

## Investigation Process

- **Started by examining log structure.** Noted that `web.log` contains HTTP request
  lines (method, path, status, user_id, request_id) and `worker.log` has job completion
  / failure entries keyed only by request_id.

- **Identified the join key.** The `request_id` field ties the two logs together. Every
  async job in the worker references the same request_id as the originating web request.

- **Counted request types in web.log.** Found 5 main POST endpoints:
  `/orders`, `/checkout`, `/cart`, `/login`, `/api/user`. Initially focused on `/orders`
  (the assignment mentioned "orders not appearing"), but `/orders` had zero worker failures.

- **Pivoted to `/checkout`.** Cross-referencing all `POST /checkout` request_ids against
  `worker_failed` revealed 2,385 failures - 100% of all worker errors.

- **Investigated unrelated errors first.** The worker log contains frequent
  `ERROR [metrics-worker] AnalyticsUploadTimeout` lines (793 occurrences). These are
  from a completely separate `metrics-worker` subsystem and have no request_id, so they
  are unrelated to the checkout incident. They were filtered out early.

- **Found the exact failure timestamp** by sorting failed checkout entries chronologically
  and identifying the first one: `2026-07-02 14:32:40.073`.

- **Examined error messages.** All 2,385 failures share the exact same error:
  `upstream call failed err=ECONNRESET upstream=10.0.3.44:8443 (retries exhausted)`.
  This was the critical clue - a single upstream IP is responsible.

- **Checked if the web tier exposed any signal.** It did not - all failing checkouts
  returned HTTP `202` with normal latency. This explained the user-visible symptom:
  the order *appeared* to succeed, but silently failed in the background.

- **Verified no recovery.** The hourly breakdown showed the failure rate climbed from
  6.5% at 14:00 to ~44% by 15:00 and held there all the way to 23:00. The incident
  was never resolved within the log window.

- **Confirmed scope of user impact.** Found 2,335 distinct affected users; 2,204 of them
  had exclusively failed checkouts with no successful order getting through.
