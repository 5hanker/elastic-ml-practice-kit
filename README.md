# Elastic ML practice kit

A practice environment for Elastic machine learning (anomaly detection). One command loads about 6 million documents of synthetic data from a made-up multi-tenant retail and ordering platform (APM traces, metrics and application logs) into your own free Elastic Cloud Serverless Observability project. It also creates six ML jobs, alert rules and a dashboard, and starts three live problems. You then follow written exercises at your own pace.

It is written for SREs, developers and architects. You do not need any ML background. No real data is involved.

## What you need

| You need | Details |
|---|---|
| An Elastic Cloud account | The free 14-day trial works. No credit card is needed. |
| An Observability serverless project | Use the **Complete** tier. The Logs Essentials tier has no machine learning. Step 1 shows how to create it. |
| An API key for that project | Created in Kibana in step 1. |
| A way to run one command | Either a computer with **Python 3.9 or newer** (check with `python3 --version` on macOS and Linux, or `py -3 --version` on Windows), **or** just a GitHub account for the no-install route. |
| About 20 minutes | Most of it is waiting. |

There is nothing to `pip install`. The kit uses only the Python standard library.

## Step 1: create the project and the API key

1. Go to <https://cloud.elastic.co/registration> and sign up for the free trial. Use an email address that is not already registered with Elastic Cloud (or add a `+alias`, for example `you+ml@example.com`).
2. Click **Create project**, choose **Observability**, then choose the **Complete** tier.
3. Pick any region and create the project. It is ready in a minute or two. Click **Continue** (or **Open Kibana**) to open the project.
4. **Copy the Kibana URL** from the browser address bar. Copy up to and including `.elastic.cloud`, with nothing after it. It looks like `https://my-project-abc123.kb.us-central1.gcp.elastic.cloud`.
5. In Kibana click the search box at the top (or press `/`), type **API keys** and open it.
6. Click **Create API key**. Name it `mlws-workshop`. Set the expiry to **at least 30 days**. Leave the key unrestricted (this is a throwaway trial project). A restricted alternative is in [docs/guide.md](docs/guide.md#restricted-role-descriptor).
7. Click **Create API key** and **copy the Encoded value**. **It is shown only once.** If you lose it, create another.

Keep the key private. Do not paste it into chat, email or a commit. See [Keeping your key safe](#keeping-your-key-safe).

## Step 2: run one command

Get the code, either way:

```
git clone https://github.com/5hanker/elastic-ml-practice-kit.git
cd elastic-ml-practice-kit
```

or click **Code**, **Download ZIP** on the GitHub page and unzip it. Then open a terminal in the unzipped folder:

| Your computer | How to open a terminal in the folder |
|---|---|
| **macOS** | Open **Terminal** (press Cmd+Space, type `Terminal`, press Enter). Type `cd ` (with a space), drag the unzipped folder from Finder into the Terminal window, and press Enter. |
| **Windows** | Open the folder in File Explorer, click the address bar, type `powershell` and press Enter. |
| **Linux** | Right-click inside the folder and choose **Open in Terminal**, or `cd` to it. |

Then run the one command for your computer:

| Your computer | Command |
|---|---|
| **macOS** | `python3 setup.py` |
| **Linux** | `python3 setup.py` |
| **Windows** | `py -3 setup.py` (or `python setup.py` if `py` is not found) |

On macOS and Linux you can also run `./setup.sh`. It finds a suitable Python for you. If it says "permission denied" (this can happen with a downloaded ZIP), run `bash setup.sh` instead.

**macOS notes.** If you have never used Python, macOS may offer to install the "Command Line Developer Tools" the first time you run `python3`. Click **Install** and wait, then run the command again. You can also install Python 3.9 or newer from <https://www.python.org/downloads/> or with `brew install python`. If setup stops with `CERTIFICATE_VERIFY_FAILED` and you installed Python from python.org, open the `Python 3.x` folder in Applications and double-click **Install Certificates.command**, then run setup again.

The examples below write `python setup.py`. Use the command from the table for your computer.

### What you will see

First a short introduction, then two questions (the key is hidden as you type), then a confirmation:

```
This will set up a hands-on machine learning exercise in the Elastic project whose URL you give:
  - installs the machine learning jobs, sample data, alert rules and dashboards (all names start with mlws-)
  - starts a few problems that develop over the next hour, so there is something to find
  - takes about 20 minutes, and is safe to run again
  - remove everything later with: python setup.py teardown

Kibana URL (e.g. https://x.kb.us-central1.gcp.elastic.cloud): https://my-project-abc123.kb.us-central1.gcp.elastic.cloud
Elastic API key (encoded, input hidden):
Project: https://my-project-abc123.kb.us-central1.gcp.elastic.cloud
Continue? [y/N] y
```

Answer `y`. (Add `--yes` to skip the question.) Setup then:

1. checks your project (project type, machine learning, key permissions),
2. loads about 6 million documents (about 6 minutes),
3. creates the ML jobs and waits for them to catch up on the history (about 10 minutes; the first job can start slowly because Elastic has to start a machine learning node),
4. creates the alert rules, the dashboard and the saved searches,
5. starts the live scenarios.

You can leave it alone while it runs. It is safe to run again if it stops halfway (for example after a network drop).

The end of a successful run looks like this (your dates and links will differ):

```
Setup summary
  [x] preflight        ok
  [x] templates        ok
  [x] data             ok (6,344,611 docs in 372s)
  [x] lifecycle        ok
  [x] jobs             ok
  [x] kibana           ok
  [x] datafeeds        ok
  [x] forecasts        ok
  [x] alerts           ok
  [x] live scenarios   ok (started cascade, info-flood, delayed)

Links
  ML jobs list                                   https://.../app/ml/jobs
  Anomaly Explorer: mlws-apm-multimetric         https://.../app/ml/explorer?...
  ...

What was started for you
Problems hidden in the past data (times are UTC):
  A1  Checkout traffic drops to 30%  (2026-09-29 17:00 to 18:30 UTC)
  A2  Order submission latency x4  (2026-10-01 23:00 to 00:30 UTC)
  ...
Problems developing now, in real time:
  payments-gateway starts failing in about 2 minutes; its alert appears about 25-35 minutes after setup finishes.
  orders-api latency starts about 15 minutes after that; its alert about 15 minutes later.
  ...
Next: open docs/guide.md and start with Exercise 1.
The sample data ends 2026-10-09 14:14 UTC; after that the low-count detectors flag empty time buckets, so finish before then and run `python setup.py teardown` when you are done.
Check everything any time with: python setup.py verify
```

A `[ ]` instead of `[x]` marks a step that failed. Read the message above it and see [Troubleshooting](#troubleshooting).

### Skip the live scenarios

Setup starts three live scenarios at the end (see [Scenarios](#scenarios-it-covers)). To load everything else without them, add `--no-live-scenarios`.

### Other ways to provide the URL and key

- **Environment variables.** macOS and Linux: `export ELASTIC_KIBANA_URL=...; export ELASTIC_API_KEY=...; python3 setup.py`. Windows PowerShell: `$env:ELASTIC_KIBANA_URL="..."; $env:ELASTIC_API_KEY="..."; py -3 setup.py`.
- **A `.env` file.** Copy `.env.example` to `.env` in the repository folder, fill in the two values and run setup. `.env` is git-ignored.
- **Flags.** `--kibana-url` and `--api-key` exist, but flags can be seen in process lists. Prefer the options above.

Precedence: flags, then environment variables, then `.env`, then the prompt.

### No local Python? Use GitHub Actions

1. On this repository's GitHub page click **Use this template**, then **Create a new repository** (private is fine).
2. In your copy open **Settings**, **Secrets and variables**, **Actions**, **New repository secret**. Add `ELASTIC_KIBANA_URL` (the URL from step 1) and `ELASTIC_API_KEY` (the encoded key). Optional: `ALERT_EMAIL`.
3. Open the **Actions** tab. If GitHub says workflows are disabled, enable them.
4. Choose **Setup workshop**, click **Run workflow**, then click the green **Run workflow** button.
5. When the run turns green (about 20 to 25 minutes), open the log. The summary and links are at the end. To print the links again, run **Verify workshop**.

Running from GitHub's network also avoids corporate proxy problems.

### Or use Codespaces

Open a Codespace on your copy of the repository, add `ELASTIC_KIBANA_URL` and `ELASTIC_API_KEY` as Codespaces secrets (or copy `.env.example` to `.env`), then run `python setup.py` in the terminal.

## What gets installed

Everything has a name starting with `mlws-` (or lives in a data stream ending `-mlws`), so the kit never touches anything else in your project.

| What | Details | Where to find it in Kibana |
|---|---|---|
| Data | About 6 million synthetic documents: 28 days of history plus about a day of normal traffic ahead of "now". Six services (`cart-api`, `payments-gateway`, `orders-api`, `admin-portal-api`, `inventory-sync`, `catalog-sync`), five app codes (`ABC`, `XYZ`, `LMN`, `PQR`, `DEF`), 200 fictional sites. Streams: APM traces, errors, three 1-minute metric streams, and application logs `logs-mlws.app-default`. | Observability **APM**, **Discover** |
| ML jobs | `mlws-apm-multimetric`, `mlws-logs-errors-correct`, `mlws-logs-errors-level-filtered`, `mlws-logs-categories`, `mlws-logs-store-volume`, `mlws-logs-inventory-health`. Optional: `mlws-terminal-population` (`--with-population`) and `mlws-apm_tx_metrics` (`--prebuild-module-job`; otherwise you build it in Exercise 2). | Search **Anomaly detection**, **Jobs** |
| Alert rules | `mlws-ml-native-record`, `mlws-ml-per-partition`, `mlws-ml-per-partition-15m`, `mlws-ml-jobs-health`. They write to the index `mlws-alerts-history`. | Search **Rules**; Discover data view **Alerts history (workshop)** |
| Dashboard and data views | Dashboard `mlws-home` ("ML Workshop - Home"), saved searches and data views. | Search **Dashboards** |
| Setup bookkeeping | Index `mlws-meta` and an index template and ingest pipeline `mlws-app-logs`. | Not needed for the exercises |

Setup also writes two local files: `mlws-setup.log` and `verify-report.json`. Both contain your project URL but never the API key.

## Scenarios it covers

Everything below is started by `python setup.py`. There is nothing to inject by hand.

### Six problems hidden in the history

Dates are relative to **D**, the UTC day you ran setup. Setup prints the real dates. Details and expected scores are in [docs/anomalies.md](docs/anomalies.md).

| ID | What happens | Which job catches it |
|---|---|---|
| A1 | `cart-api` traffic falls to 30% (D-9) | `mlws-apm-multimetric` |
| A2 | `orders-api` order submission latency x4 (D-7) | `mlws-apm-multimetric` |
| A3 | Site `S-0117` goes completely silent (D-5) | `mlws-logs-store-volume` |
| A4 | `payments-gateway` fails 25% of authorisations with issuer timeouts (D-4) | `mlws-apm-multimetric`, `mlws-logs-errors-level-filtered` |
| A5 | Catalog sync rejections logged at **INFO** level (D-2) | `mlws-logs-errors-correct`, `mlws-logs-categories`. **Not** the level-filtered job. |
| A6 | Inventory queue grows from 50 to 6000, then admin portal latency x3 (D-1) | `mlws-logs-inventory-health`, `mlws-apm-multimetric`, `mlws-logs-categories` |

### Three live scenarios

| Scenario | What it does | What it teaches |
|---|---|---|
| `cascade` | `payments-gateway` starts failing about 2 minutes after setup ends. `orders-api` latency rises about 15 minutes after that. | One alert per job (native rule) versus one alert per service (query rule). Exercise 4. |
| `info-flood` | A burst of INFO-level failures from the catalog sync app. | A level-only filter misses it, an error-ness filter does not. Exercise 3. |
| `delayed` | Documents that arrive late, stamped with times in the past. | Delayed-data warnings and annotations, and `query_delay`. Exercise 3. |

Alerts from the live scenarios appear roughly 25 to 35 minutes (`payments-gateway`) and about 50 minutes (`orders-api`) after setup ends. Do Exercises 1 to 3 first, then come back to Exercise 4.

## What to do next

Open [docs/guide.md](docs/guide.md) and work through Exercises 1 to 4.

## Check it worked

```
python setup.py verify
```

It counts the data, checks the jobs and datafeeds, and checks that each hidden problem scored high (and that the level-filtered job missed A5). It writes `verify-report.json`. Add `--allow-pending` if jobs are still catching up. Fresh jobs can show "catching up" for up to 15 minutes; that is normal.

## Clean up

1. Run `python setup.py teardown` (type `yes` when asked, or add `--yes`). This removes everything setup created.
2. In Kibana search **API keys** and **Invalidate** the key.
3. Delete the Observability project in the Elastic Cloud console.

The generated data ends about a day after setup, and the live scenarios end within about an hour. After that the low-count detectors report empty buckets. Run `python setup.py setup --reload` for a fresh window, or tear down.

## Troubleshooting

More in [docs/guide.md](docs/guide.md#troubleshooting).

| Problem | Fix |
|---|---|
| `unexpected redirect to <host>` or the URL is rejected | The Kibana URL is wrong. Use the project's own URL with no path after `.elastic.cloud`. |
| `401`, `403` or `privileges: missing` | The key expired, is not the **Encoded** value, belongs to another project, or is too restricted. Create a new unrestricted key and run again. |
| `Machine learning is not available` or ML pages are missing | Wrong tier. Create a new Observability project on the **Complete** tier. |
| `python` not found, version below 3.9, or timeouts and certificate errors | Install Python 3.9 or newer from python.org, or use the GitHub Actions route. Do not disable certificate checks. |
| Setup stopped halfway | Run `python setup.py` again. It skips finished steps and never duplicates data. |
| Jobs show 0 results or a warning that no ML node is running | The first ML node starts cold and takes a few minutes. Open **Anomaly detection, Jobs**, wait, then run `python setup.py verify --allow-pending`. |

## Command reference

Run as `python3 setup.py <command>` on macOS and Linux, or `py -3 setup.py <command>` on Windows (the examples in this README write `python setup.py`). Every command supports `--help`.

| Command | What it does |
|---|---|
| `setup` (the default) | Preflight, templates, data, jobs, rules, Kibana content, live scenarios, summary with links. Flags: `--yes`, `--no-live-scenarios`, `--reload`, `--days N`, `--scale X`, `--with-population`, `--prebuild-module-job`, `--no-wait`. |
| `preflight` | Checks only: project type, ML available, key privileges, ML node. |
| `verify` | Checks data, jobs and expected anomaly scores. `--allow-pending` tolerates jobs still catching up. |
| `links` | Prints the Kibana links again. |
| `inject [cascade\|info-flood\|delayed]` | Optional. Replays a live scenario. Setup already ran all three. See [docs/anomalies.md](docs/anomalies.md). |
| `anomalies` | Lists the hidden problems. `--md` prints a table, `--t0 <ISO time>` resolves real dates. Needs no cluster. |
| `teardown` | Removes everything setup created (asks first; `--yes` skips the question). |

GitHub workflows (for the Actions route): **Setup workshop**, **Verify workshop**, **Inject anomaly** and **Teardown workshop** (type `DELETE` to confirm).

## Repository layout

```
setup.py, setup.sh, setup.ps1   entry points (Python 3.9+, standard library only)
setup/                          the command line tool
data/                           synthetic data generators and the hidden problems
jobs/                           ML job definitions, with UI steps and what each teaches
alerts/                         connector and rule definitions
kibana/                         data views, saved searches and the dashboard
templates/                      index template and ingest pipeline for the app logs
docs/                           the exercise guide and the list of hidden problems
.github/workflows/              GitHub Actions workflows
.devcontainer/                  Codespaces configuration
```

| Document | Purpose |
|---|---|
| [docs/guide.md](docs/guide.md) | Step-by-step exercises and troubleshooting |
| [docs/anomalies.md](docs/anomalies.md) | The hidden problems, expected scores, live scenarios |

## Keeping your key safe

- The API key is read from the prompt, an environment variable or `.env`. It is never printed, never written to a log or report file, and never sent anywhere except your own project.
- `.env`, `mlws-setup.log` and `verify-report.json` are git-ignored. The log and report contain your project URL but not the key. Do not commit them anyway.
- Use a throwaway trial project, set the key to expire (at least 30 days is enough), and invalidate it when you finish (Kibana, search **API keys**, **Invalidate**).
- If a key leaks: invalidate it right away in Kibana, create a new one and update any GitHub secret that uses it.

## License

Apache-2.0. See [LICENSE](LICENSE).
