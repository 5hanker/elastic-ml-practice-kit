# Guide: learn Elastic ML anomaly detection with this kit

This guide takes you through four numbered exercises and a few optional ones, using the data and jobs that `python setup.py` created in your own Serverless Observability project. If you have not run setup yet, start with the [README](../README.md). Work at your own pace. You can stop after any exercise and come back.

Menus in Serverless Observability move between releases. The most reliable way to reach any page is the **global search** at the top of Kibana. Press `/` or click the search box and type the page name (for example "Anomaly detection", "API keys", "Rules", "Discover").

All times in this guide are UTC.

## Contents

1. [Before you start](#before-you-start)
2. [Check that it worked](#check-that-it-worked)
3. [The hidden problems](#the-hidden-problems)
4. [Exercise 1: Read the Anomaly Explorer](#exercise-1-read-the-anomaly-explorer)
5. [Exercise 2: Build a job with the module wizard](#exercise-2-build-a-job-with-the-module-wizard)
6. [Exercise 3: Is my job even looking?](#exercise-3-is-my-job-even-looking)
7. [Exercise 4: Why did the other service not alert?](#exercise-4-why-did-the-other-service-not-alert)
8. [Optional exercises](#optional-exercises)
9. [Troubleshooting](#troubleshooting)
10. [Restricted role descriptor](#restricted-role-descriptor)
11. [When you are done](#when-you-are-done)

## Before you start

You need a Serverless Observability project (Complete tier) with setup finished. Setup creates the data, six ML jobs, alert rules and a dashboard, and it starts three live scenarios. If you have not done that, follow the [README](../README.md), steps 1 and 2.

When you can start each exercise:

| Exercise | When |
|---|---|
| 1 Read the Anomaly Explorer | Immediately after setup |
| 2 Build a job with the module wizard | Immediately |
| 3 Is my job even looking? | Immediately for the history. The live `info-flood` and `delayed` parts need about 30 to 60 minutes. |
| 4 Why did the other service not alert? | **Alerts appear 25 to 50 minutes after setup ends** (about 25 to 35 minutes for `payments-gateway`, about 50 minutes for `orders-api`). Do Exercises 1 to 3 first. |

Good to know:

- Everything is synthetic. No production data is used.
- **Data lifetime.** The generated data ends about a day after setup. Afterwards the low-count detectors flag empty buckets. Run `python setup.py setup --reload` for a fresh window, or tear down when you are done.
- **Dates.** Windows are relative to **D**, the UTC day setup ran. "D-9 17:00" means 17:00 UTC nine days before D. Setup printed the real dates. You can also list them with `python setup.py anomalies --t0 <setup time>` (for example `--t0 2026-10-08T12:00:00Z`).
- **Where to find the commands.** The `python setup.py ...` commands in this guide are run from the repository folder, the same place you ran setup.

## Check that it worked

- Run `python setup.py verify` (add `--allow-pending` while jobs are still catching up).
- Or look in the UI. Search **Anomaly detection**, then **Jobs**. You should see jobs whose ID starts with `mlws-`, all **started**.
- Typical scores (yours can differ by a few points): A1 99, A2 about 91 to 94, A3 100, A4 100 (APM job) and 99 (log job), A5 99 (`mlws-logs-errors-correct`) and 100 (`mlws-logs-categories`), A5 **not caught** by the level-filtered job (that is the lesson of Exercise 3), A6 93 to 100.

## The hidden problems

The data has six hidden problems. Each has a window relative to D (see above).

| ID | When | Where | What happens | Best caught by |
|---|---|---|---|---|
| A1 | D-9, 17:00, 90 min | `cart-api` | Throughput falls to 30% | `mlws-apm-multimetric` |
| A2 | D-7, 23:00, 90 min | `orders-api`, `POST /api/v1/orders` | Latency x4 | `mlws-apm-multimetric` |
| A3 | D-5, 16:30, 120 min | site `S-0117` | The site goes silent: no logs, no transactions | `mlws-logs-store-volume` |
| A4 | D-4, 17:00, 60 min | `payments-gateway` / `XYZ` | 25% of payment authorisations fail with issuer timeouts | `mlws-apm-multimetric`, `mlws-logs-errors-level-filtered` |
| A5 | D-2, 16:00, 120 min | app code `ABC` (catalog sync) | "Catalog sync ... rejected: HTTP 422 ... will retry" logged at **INFO** | `mlws-logs-errors-correct`, `mlws-logs-categories` (**not** the level-filtered job) |
| A6 | D-1, 22:30, 150 min | `inventory-sync` then `admin-portal-api` | Inventory queue grows from 50 to 6000, then portal latency x3 | `mlws-logs-inventory-health`, `mlws-apm-multimetric`, `mlws-logs-categories` |

A window that would end too close to "now" is shifted back one day. More detail: [anomalies.md](anomalies.md).

## Exercise 1: Read the Anomaly Explorer

**Goal:** find the planted problems in a finished job and see how typical and actual values differ. **Job:** `mlws-apm-multimetric`.

1. **Swim lanes.** Global search **Anomaly detection**, then **Anomaly Explorer**. Choose the job `mlws-apm-multimetric` and set the time range to the **last 30 days** (or use the link printed at the end of setup). The top lane is the overall timeline; the lane below is split by service. Red and orange cells are high scores. Find:
   - **A1**: `cart-api`, traffic drop, about D-9 17:00. Score about 99.
   - **A2**: `orders-api`, latency x4, about D-7 23:00. Score about 91 to 94.
   - **A4**: `payments-gateway`, failed transactions, about D-4 17:00. Score 100.

   Click a red cell to filter the table below.
2. **Influencers.** On the left read **Top influencers** for `service.name`, `transaction.name` and `host.name`. Which service and which transaction is to blame for A2? (`orders-api`, `POST /api/v1/orders`.)
3. **Actual versus typical.** In the anomalies table open a row (chevron) and compare **actual** and **typical**. Open the explanation: single-bucket impact, multi-bucket impact, anomaly characteristics.
4. **Single Metric Viewer.** Use the row action **View series**. The shaded band is the model plot: the range the model expected. Points outside it are anomalies.
5. **Forecast.** Click **Forecast**, choose **3 days** and run it. A forecast shows the **trend**, not future anomalies. Forecasts are not available for population jobs.
6. **Job group.** Back in the Anomaly Explorer select the job group **`mlws`** (all workshop jobs). You now see the A6 story across APM and logs: the inventory queue first, then the log errors, then the portal latency.

**What to look for:** three distinct problems in one job, each attached to the right service, with scores of 75 or more (critical).

**Why it matters:** one multi-metric job split by `service.name` gives each service its own baseline for latency, throughput and failures without a threshold per service. The influencer list answers "where do I look first?".

> **Young-model anomalies.** You may see a high-scoring `cart-api` latency anomaly around mid-September, early in the data. It is not planted. It appears because the model had only about a week of history then, so it was less certain. Real jobs behave the same way: give a new job two or more weeks of history before you trust its alerts.

## Exercise 2: Build a job with the module wizard

**Goal:** build and start a job with the recognized-module wizard. **Data:** `metrics-apm.transaction.1m-mlws`.

1. **Create job.** Global search **Anomaly detection**, **Jobs**, **Create job**.
2. **Pick the data view.** Choose **APM (workshop)**. The APM module is recognized on this data view, and on the index pattern `metrics-apm.transaction.1m-mlws`.
3. **Use the recognized module.** Kibana offers **APM: Transaction data** under "Use preconfigured jobs". Choose it.
4. **Set the job ID prefix to `mlws-`.** This matters: teardown only removes `mlws-` items. The job becomes `mlws-apm_tx_metrics`.
5. **Start it.** Choose **Start at the beginning of the data** (pick a time before D-9 to include A1) and **Use real-time**. Click **Create jobs**.
6. **Peek.** Open the results link. Do not wait for it to finish: go on to Exercise 3 and come back. Compare its detectors and influencers with `mlws-apm-multimetric`.

If you ran setup with `--prebuild-module-job`, `mlws-apm_tx_metrics` already exists. Open it and read its settings instead.

**What to look for:** the module chose detectors, influencers, bucket span (15 minutes) and a model memory estimate for you. It reads the 1-minute pre-aggregated metrics, which is cheaper than scanning every raw transaction.

**Why it matters:** if your APM data is on the standard schema, a module gets you a working job in minutes. The two common mistakes are forgetting the prefix and forgetting real-time; both can be fixed by editing the job.

## Exercise 3: Is my job even looking?

**Goal:** prove that a running, green job can still be blind. **Jobs:** `mlws-logs-errors-correct` and `mlws-logs-errors-level-filtered`.

The two jobs are twins: same detector (`high_count` per `app_code`), same influencers. The difference is the datafeed query.

- **`mlws-logs-errors-correct`** filters on **error-ness**: `error_code` exists, **or** `level` is error, critical or fatal. Note that the fix for a level-only filter is error-ness, not "no filter".
- **`mlws-logs-errors-level-filtered`** is identical except for its datafeed query, which filters on `level` only.

**The incident is A5.** About D-2 16:00, for 2 hours, the `ABC` app code (catalog sync) logs "Catalog sync ... rejected: HTTP 422 ... will retry" messages at **INFO** level.

1. **Datafeed preview.** **Anomaly detection, Jobs.** For each twin expand the job row (chevron) and open the **Datafeed preview** tab. Compare what each job is fed. The correct job receives the ABC documents; the filtered job receives none of the A5 ones because they are INFO.
2. **Results.** Open both jobs in the Anomaly Explorer around D-2 16:00. `mlws-logs-errors-correct` flags ABC at about 99. `mlws-logs-errors-level-filtered` stays low (25 or less). The filtered job still catches real ERROR spikes such as A4 (`XYZ`) and A6 (`DEF`), which is why it looks healthy.
3. **Counts tab.** In the jobs list expand a job, open **Job details**, then the **Counts** tab. Read `processed_record_count`, `empty_bucket_count`, `sparse_bucket_count` and `latest_record_timestamp`. This is the direct "is it working?" answer: a stuck `latest_record_timestamp` or many empty buckets means the job is not seeing data.
4. **Job messages.** In the same place open **Job messages**. Look for datafeed, delayed-data or memory messages.
5. **Annotations.** Open the **Annotations** tab or the Single Metric Viewer. Delayed-data annotations mark buckets where documents arrived after the datafeed had already read them. Setup already started the `delayed` scenario, so after about 30 to 60 minutes you should see a delayed-data message and annotation for `XYZ` logs. Come back to this step later.

The live `info-flood` scenario (started by setup) is the same lesson in real time: about 30 minutes after setup ends, `mlws-logs-errors-correct` scores the new `ABC` burst and `mlws-logs-errors-level-filtered` does not.

**What to look for:** the same incident, one job alerting, one silent, and no error anywhere. `mlws-logs-categories` also scores A5 at about 100 with a brand-new message category.

**Why it matters:** a level filter misses problems logged at the wrong severity or with different casing (`error` versus `ERROR`). Always preview the datafeed and compare it with what you see in Discover. Delayed data means `query_delay` is too small for your ingest lag: size it from the p99 of `event.ingested - @timestamp`, not from a default.

## Exercise 4: Why did the other service not alert?

**Goal:** compare a native ML rule with a per-partition Elasticsearch query rule. **Rules:** `mlws-ml-native-record`, `mlws-ml-per-partition`, `mlws-ml-per-partition-15m`.

**Timing.** Setup started the `cascade` scenario when it finished. `payments-gateway` starts failing about 2 minutes after setup ends and `orders-api` latency rises about 15 minutes after that. Alerts appear roughly 25 to 35 minutes (`payments-gateway`) and about 50 minutes (`orders-api`) after setup ends. Do Exercises 1 to 3 first. If the live window has passed, run `python setup.py inject cascade` to replay it and allow another 60 to 75 minutes. Both services are covered by the same job, `mlws-apm-multimetric`.

1. **Find the rules.** Global search **Rules**. Four rules are tagged `mlws`: `mlws-ml-native-record`, `mlws-ml-per-partition`, `mlws-ml-per-partition-15m` and `mlws-ml-jobs-health`. Confirm they are enabled. In Serverless Observability native ML rules are tied to the job and may not appear on the main Alerts page, so use the Rules page.
2. **Wait for buckets to close.** Results appear after the 15-minute bucket ends plus `query_delay` (90 seconds). Until then the alert history is empty. That is expected.
3. **Compare the alert history.** Global search **Discover** and choose the data view **Alerts history (workshop)** (index `mlws-alerts-history`). Each rule writes a document per alert. Search for `mlws-ml-native-record` and `mlws-ml-per-partition` and count the documents:
   - `mlws-ml-native-record`: **one alert per job**. The alert instance is the job ID, so the second service is folded into the first.
   - `mlws-ml-per-partition`: **one alert per service**. The rule is scoped to `mlws-apm-multimetric` and grouped by `job_id` + `partition_field_value`, so `payments-gateway` and `orders-api` each get their own alert, and each recovers on its own.
4. **Edit and inspect the per-partition rule.** Open `mlws-ml-per-partition` and choose **Edit**. Find the query (`.ml-anomalies-*`, filters `result_type: record`, `record_score >= 75`, `is_interim: false` and the job), the time window (30 minutes), the **group by** setting (top, on the fields `job_id` and `partition_field_value`) and the action that writes to the `mlws-alerts` connector. Do not save changes you do not want; re-running setup recreates the rule.
5. **Read the alert document.** In Discover open a document from `mlws-ml-per-partition`. The `records` field holds one entry per hit with job, partition, `record_score`, actual, typical, function and field, timestamp, influencers and an Anomaly Explorer link. The point: the person on call can act from the notification alone. The native rule's context has the score, top influencers, top records and the Explorer link.
6. **Compare 30 minutes with 15 minutes.** `mlws-ml-per-partition` looks back 30 minutes and `mlws-ml-per-partition-15m` looks back 15 minutes. In testing the 15-minute variant wrote no alerts, which is expected. Final records are timestamped at the bucket start and written after the bucket end plus `query_delay`, so a 15-minute window looks too late. The rule of thumb: look-back of at least 2 x bucket span plus `query_delay`.

**What to look for:** one notification for two different services versus two notifications you can route separately.

**Why it matters:** if one job covers many services, the native rule hides the second, third and fourth problem while the first is still active. A query rule over `.ml-anomalies-*` grouped by `partition_field_value` gives one alert per service. Also add the jobs-health rule (`mlws-ml-jobs-health`) so you learn when a datafeed stops, memory runs out, data arrives late or a job reports errors (it does not check "behind real time").

## Optional exercises

- **Categorization.** Open `mlws-logs-categories` in the Anomaly Explorer around A5 (`ABC`) and A6 (`DEF`). The job uses per-partition categorization of `message` by `app_code` with a count and a `rare` detector, so a brand-new message type ("Catalog sync ... rejected", "Inventory delta rejected") scores high without anyone writing a rule. Compare with the UI steps in `jobs/30-mlws-logs-categories.json`.
- **Store-volume job.** Open `mlws-logs-store-volume` and find A3: site `S-0117` goes silent. `low_count` models empty buckets, so a silent site is detected; the site is large, so its drop is large against its own baseline. Open the Single Metric Viewer for the partition.
- **Terminal population.** Run `python setup.py setup --with-population --reload` (population data is generated at load time, so a reload is needed; this takes the full setup time again). This adds `mlws-terminal-population` and a seventh problem (A7, one terminal reporting printer errors). Population jobs compare each entity with its peers instead of its own history. Forecasts are not available for them.
- **Replay a live scenario.** Setup already started all three. To replay one, run `python setup.py inject cascade` (or `info-flood`, or `delayed`). Details are in [anomalies.md](anomalies.md).
- **Build the other jobs yourself.** Each `jobs/*.json` has a `ui_path` field with the wizard clicks to recreate the job. Use a different prefix to avoid clashing with the existing job IDs, and delete your copies afterwards.


## Troubleshooting

| Message or symptom | Cause | Fix |
|---|---|---|
| `this does not look like an Observability project` | Wrong project type (Search or Security) | Create a new **Observability** project. |
| `Machine learning is not available` or ML pages missing | The **Logs Essentials** tier has no ML | Create a new Observability project on the **Complete** tier. |
| `not serverless` | The URL points at a Hosted deployment | Use a Serverless Observability project. `--allow-non-serverless` exists but is untested. |
| `privileges: missing: ...` | The API key is too restricted | Create a new key without restrictions (throwaway trial project) or use the role below, then run setup again. |
| `401` or `403` from the cluster | Key expired, wrong value (not the **Encoded** one), or wrong project | Create a new key, copy the **Encoded** value, run setup again. |
| `unexpected redirect to <host>` | The Kibana URL is wrong | Use the project's Kibana URL with nothing after `.elastic.cloud`. |
| `no ML node running yet` (WARN) | Serverless ML node cold start | Not an error. The first job start takes a few minutes. Open **Anomaly detection, Jobs**, and wait. |
| Jobs show 0 results although the datafeed is "started" | Still catching up to real time | Wait up to 15 minutes after the load, then run `python setup.py verify --allow-pending`. |
| Exercise 4 alert history is empty | The 15-minute buckets for the live scenario have not closed yet | Wait until 25 to 50 minutes after setup ended. If it is long past, replay with `python setup.py inject cascade`. |
| Many empty buckets or low-count anomalies on the newest data | The generated data ended about a day after setup | Run `python setup.py setup --reload --yes`, or tear down. |
| Timeouts or certificate errors from your computer | Proxy or TLS inspection | Use the GitHub Actions route in the README. Do not disable certificate checks. |
| `python` is not found, or the version is below 3.9 | Python missing or old | Install Python 3.9 or newer from python.org. On macOS and Linux use `python3 setup.py`; on Windows try `py -3 setup.py`. Or use the GitHub Actions route. |
| Setup stopped halfway | Network drop or rate limit | Run `python setup.py` again. It skips finished steps and never duplicates data. |
| GitHub Actions route: no **Run workflow** button | Template copies start with Actions off | Open the **Actions** tab and enable workflows. Make sure you are on the default branch. |
| GitHub Actions route: `Missing repository secrets: ...` | Secret names wrong or not set | Names must be exactly `ELASTIC_KIBANA_URL` and `ELASTIC_API_KEY`. |
| You want a clean start | | Run `python setup.py teardown`, then `python setup.py` again. |

## Restricted role descriptor

> **Status: not validated.** Not yet tested on a fresh trial. This role was derived from the privileges the setup script checks. To use it, turn on **Control security privileges** when you create the API key and paste the JSON. If preflight reports a missing privilege, use an unrestricted key on a throwaway project instead.

```json
{
  "mlws-workshop": {
    "cluster": ["manage_ml", "manage_index_templates", "manage_ingest_pipelines", "monitor"],
    "indices": [
      {
        "names": [
          "logs-mlws.*",
          "traces-apm-mlws",
          "metrics-apm.*-mlws",
          "logs-apm.error-mlws",
          "mlws-*"
        ],
        "privileges": [
          "create_doc", "create_index", "auto_configure", "write", "read",
          "view_index_metadata", "manage", "delete_index"
        ]
      },
      {
        "names": [".ml-anomalies-*", ".ml-notifications*", ".ml-annotations*"],
        "privileges": ["read", "view_index_metadata"]
      }
    ],
    "applications": [
      {
        "application": "kibana-.kibana",
        "privileges": [
          "feature_ml.all",
          "feature_actions.all",
          "feature_stackAlerts.all",
          "feature_apm.all",
          "feature_discover_v2.all",
          "feature_dashboard_v2.all",
          "feature_visualize_v2.all",
          "feature_indexPatterns.all",
          "feature_savedObjectsManagement.all"
        ],
        "resources": ["space:default"]
      }
    ]
  }
}
```

## When you are done

- Run `python setup.py teardown` (or the **Teardown workshop** workflow, typing `DELETE`).
- **Invalidate the API key** (search **API keys**), delete any repository secrets you added, and delete the project.
