# Hidden problems and live scenarios

The data has six hidden problems (A1 to A6) and one optional seventh (A7). This page lists them, shows how to find their real dates, and explains the three live scenarios that setup starts for you.

## How the dates work

All windows are **relative to D, the UTC day you ran `python setup.py`**. "D-9 17:00" means 17:00 UTC, nine days before D.

- Times are **UTC**. For example, 17:00 UTC is 22:30 in India (IST) and 18:00 in London in winter.
- A window that would end too close to "now" is shifted back one day, so the actual date can be one day earlier than the offset suggests.
- **Setup prints the real UTC dates** and the Kibana links at the end of its output. To print the links again, run `python setup.py links`. To resolve the dates yourself, run `python setup.py anomalies --t0 <setup time in ISO 8601 UTC>`.
- The Anomaly Explorer links use a 30-day time range, so every hidden problem is inside it.

## The table

The rows below are generated with `python setup.py anomalies --md` (it reads `data/`, needs no cluster). The "Window" column shows the offset from D and the duration. To resolve actual dates for a known setup time, add `--t0`, for example `python setup.py anomalies --md --t0 2026-10-08T12:00:00Z`. "Caught by" lists the jobs that must score the anomaly at 75 or more. "Must NOT be caught by" is deliberate: A5 is the lesson of Exercise 3 in the guide.

| ID | Window (UTC) | Entity | What changed | Caught by | Must NOT be caught by |
|---|---|---|---|---|---|
| A1 | D-9 17:00, 90 min | cart-api | Transactions per minute x0.3 (APM and XYZ logs from cart-api). | `mlws-apm-multimetric` / cart-api (score >= 75) | - |
| A2 | D-7 23:00, 90 min | orders-api POST /api/v1/orders | Transaction latency x4. | `mlws-apm-multimetric` / orders-api (score >= 75) | - |
| A3 | D-5 16:30, 120 min | site S-0117 | Zero logs and zero transactions for the site. | `mlws-logs-store-volume` / S-0117 (score >= 75) | - |
| A4 | D-4 17:00, 60 min | payments-gateway / XYZ | Failure rate 25%; APM errors "IssuerTimeoutException: issuer did not respond (code 91)"; XYZ ERROR logs lift total XYZ volume x3. | `mlws-apm-multimetric` / payments-gateway (score >= 75); `mlws-logs-errors-level-filtered` / XYZ (score >= 75) | - |
| A5 | D-2 16:00, 120 min | app_code ABC | Extra INFO logs "Catalog sync to site {site} rejected: HTTP 422 payload validation failed, will retry (attempt {n})" with error_code CS-422 / CatalogPayloadRejected; ABC volume x8. | `mlws-logs-errors-correct` / ABC (score >= 75); `mlws-logs-categories` / ABC (score >= 75) | `mlws-logs-errors-level-filtered` / ABC (score <= 25) |
| A6 | D-1 22:30, 150 min | inventory-sync -> admin-portal-api | DEF queue ramps 50 -> 6000, syncDuration x10, ERROR "Inventory delta rejected: cannot parse SKU payload '{garbage}' for company {company}" (>=20/min); from +45m admin-portal-api latency x3 and LMN ERROR logs x6. | `mlws-logs-inventory-health` / DEF (score >= 75); `mlws-logs-errors-level-filtered` / DEF (score >= 75); `mlws-apm-multimetric` / admin-portal-api (score >= 75); `mlws-logs-categories` / DEF (score >= 75) | - |
| A7 (population only) | D-3 12:00, 60 min | terminal T-0117-04 | ERROR "Printer offline on terminal {t}, retry {n}" at 30/min. | `mlws-terminal-population` / T-0117-04 (score >= 75) | - |

A7 exists only when setup was run with `--with-population`.

## Expected scores

Measured on a Serverless Observability project in a fresh-trial end-to-end run. Your numbers can differ by a few points.

| ID | Job | Expected score |
|---|---|---|
| A1 | `mlws-apm-multimetric` | about 99 |
| A2 | `mlws-apm-multimetric` | about 91 to 94 |
| A3 | `mlws-logs-store-volume` | 100 |
| A4 | `mlws-logs-errors-level-filtered` (log job) | about 99 |
| A4 | `mlws-apm-multimetric` (payments-gateway failures) | 100 |
| A5 | `mlws-logs-errors-correct` | about 99 |
| A5 | `mlws-logs-categories` | 100 |
| A5 | `mlws-logs-errors-level-filtered` | not caught (low score). This is the lesson. |
| A6 | `mlws-logs-inventory-health` and others | 93 to 100 |

Check your own project with `python setup.py verify`.

## Live scenarios

The six problems above are already in the history. The **live scenarios** are different: they add new problems that start just after setup finishes, so you can watch real alerts fire. **Setup starts all three automatically at the end.** You do not need to run anything. Use `--no-live-scenarios` if you do not want them.

| Scenario | What it does | What it teaches | When you see results |
|---|---|---|---|
| `cascade` | `payments-gateway` failure rate 25% from about 2 minutes after setup ends, for 45 minutes. `orders-api` latency x4 from about 17 minutes after setup ends, for 45 minutes. | Exercise 4: one alert per job (native ML rule) versus one alert per service (query rule). | `payments-gateway` alerts about 25 to 35 minutes after setup ends, `orders-api` about 50 minutes. |
| `info-flood` | A burst of INFO-level `ABC` logs from about 2 minutes after setup ends, for 60 minutes. | Exercise 3: the correct job scores it, the level-only job does not. | Within about 30 minutes. |
| `delayed` | Writes `XYZ` logs stamped 45 to 30 minutes in the past. The real-time datafeed has already passed that window. | Exercise 3: a delayed-data message and annotation. | Up to about 15 minutes after the delayed-data check runs; allow 30 to 60 minutes. |

### Replay a scenario (optional)

If you missed the live window, or want to see a scenario again, replay it. Results appear again after the timings below.

```
python setup.py inject cascade      # default
python setup.py inject info-flood
python setup.py inject delayed
```

Without a local clone, open **Actions**, pick **Inject anomaly**, choose **Run workflow** and select the scenario.

### Timing details

- Results appear after the 15-minute bucket ends, plus `query_delay` (90 seconds), plus a little time to index the result.
- In `cascade` the second service starts 15 minutes after the first.
- The `mlws-ml-per-partition` rule looks back 30 minutes and runs every minute. The 15-minute variant writes no alerts: final records are timestamped at the bucket start and written after the bucket end plus `query_delay`.
- After a replay, allow about 35 minutes for `info-flood` and 60 to 75 minutes before both `cascade` alerts are visible.
- The delayed-data check runs every 15 minutes or every check window, whichever is smaller.
- A scenario replaces the normal future traffic for the affected entities in its window, so you see a clean anomaly rather than a mix.
