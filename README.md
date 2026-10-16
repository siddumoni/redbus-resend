# RedBus Availability Watcher

A Python watcher for **RedBus bus availability**, with a train-watch configuration path under development. It checks configured routes, compares each successful fetch with saved state, and sends a single HTML email digest through [Resend](https://resend.com) when newly available options are detected.

The project runs as a **manually triggered GitHub Actions workflow** or can be run locally. It is not currently configured with a scheduled cron trigger.

## Current capabilities

| Area | Current status |
|---|---|
| Bus availability lookup | Implemented using the observed RedBus city-suggestion and bus-search request schema |
| Bus details | Normalizes operator, bus type, seat availability, fare, departure/arrival times and related fields |
| Bus filters | Supports seat-type and operator filtering |
| Multi-route and multi-date watches | Supported through `REDBUS_WATCHES` |
| Availability change detection | Compares current results with saved state to identify newly available options and avoid repeated alerts for unchanged results |
| Email notifications | Sends a combined HTML digest using Resend; includes the current options for affected watches and marks newly available options |
| Failure handling | Tracks consecutive fetch failures and can send a separate failure alert after a configurable threshold |
| Train availability lookup | **Not yet fully implemented for live use.** The parser has defensive/schema-tolerant logic, but the live train request adapter intentionally fails closed until the request and response schema are verified |
| Automated schedule | Not configured; the workflow is triggered manually |

> **Important:** Do not rely on train watches for live notifications until the actual RedBus train request has been captured, validated, implemented and tested end-to-end. The watcher must not interpret an unrecognized train response as confirmed zero availability.

## How it works

1. Read and validate the JSON watch list from `REDBUS_WATCHES`.
2. Load the previous snapshot from `state.json`.
3. Fetch each configured watch. The bus scraper resolves source and destination cities and requests bus-search results through a browser session. The train scraper currently raises an explicit error until a verified train adapter is implemented.
4. Compare a successful fetch with the previous snapshot to identify newly available options.
5. If a watch has new availability, add its full current options to a combined HTML email, marking new options with a `NEW` badge.
6. If a fetch fails repeatedly, preserve its last successful snapshot and send a separate fetch-failure alert when the failure threshold is reached.
7. Save the updated state. The GitHub Actions workflow commits `state.json` back to the repository so state persists across workflow runs.

## Repository structure

```text
.
├── .github/
│   └── workflows/
│       └── redbus-watch.yml       # Manual GitHub Actions workflow
├── redbus_watch/
│   ├── config.py                  # REDBUS_WATCHES parsing and validation
│   ├── main.py                    # Orchestration and change detection
│   ├── notifier.py                # Resend email delivery
│   ├── email_template.py          # HTML email and failure-alert templates
│   ├── scraper_bus.py             # Bus search adapter
│   ├── scraper_train.py           # Train adapter (live request not yet implemented)
│   └── state.py                   # Persistent snapshot and failure tracking
├── tests/
│   ├── fixtures/                  # Captured RedBus bus API responses
│   └── test_*.py                  # Unit and regression tests
├── discover.py                    # Interactive network-response discovery tool
├── requirements.txt
├── pytest.ini
└── state.json                     # Last-known watcher state
```

## Requirements

- Python 3.12 (the GitHub Actions workflow uses Python 3.12)
- Chromium installed through Playwright
- A Resend account and sender address for email alerts
- RedBus access from the runtime environment

Install dependencies locally:

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Configure a watch list

Set the repository variable `REDBUS_WATCHES` in **Settings → Secrets and variables → Actions → Variables**. The value must be a JSON array.

A bus example:

```json
[
  {
    "type": "bus",
    "source": "Chennai",
    "destination": "Coimbatore",
    "dates": ["2026-12-12", "2026-12-13"],
    "seat_type": "sleeper",
    "operators": ["SRS", "KPN"],
    "label": "Chennai to Coimbatore"
  }
]
```

A single date may be specified as `"date": "YYYY-MM-DD"`; use either `date` or `dates`, not both. Each date is tracked as a separate watch.

| Field | Required | Purpose |
|---|---|---|
| `type` | Yes | `"bus"` or `"train"` |
| `source` | Yes | City name for a bus watch; station name/code for a train watch |
| `destination` | Yes | Destination city or train station |
| `date` | One of `date`/`dates` | One travel date in `YYYY-MM-DD` format |
| `dates` | One of `date`/`dates` | List of dates in `YYYY-MM-DD` format |
| `label` | No | Human-friendly label shown in the alert email |
| `seat_type` | No; bus only | `any`, `seater`, `sleeper`, `ac` or `non_ac`; defaults to `any` |
| `operators` | No; bus only | List of operator-name substrings; matching is case-insensitive |
| `classes` | No; train only | Train class codes such as `SL`, `3A`, `2A`; defaults to all returned classes when the adapter is implemented |

For now, configure **bus watches for live monitoring**. A train entry can be parsed by configuration, but the live train fetch is not ready for dependable notifications.

### Local configuration example

In PowerShell:

```powershell
$env:REDBUS_WATCHES = '[{"type":"bus","source":"Chennai","destination":"Coimbatore","date":"2026-12-13","seat_type":"sleeper"}]'
$env:RESEND_API_KEY = "re_..."
$env:RESEND_FROM = "alerts@your-verified-domain.example"
$env:RESEND_TO = "you@example.com"

python -m redbus_watch.main
```

Use a sender address/domain configured and verified in Resend. Do not commit API keys or other credentials to the repository.

## GitHub Actions setup

The workflow is `.github/workflows/redbus-watch.yml`.

1. Add the repository variable `REDBUS_WATCHES` under **Settings → Secrets and variables → Actions → Variables**.
2. Add these repository secrets under **Settings → Secrets and variables → Actions → Secrets**:
   - `RESEND_API_KEY`
   - `RESEND_FROM`
   - `RESEND_TO`
3. Open **Actions → RedBus Availability Watcher → Run workflow** and run it on `main`.
4. Inspect the logs and confirm the run summary and Resend delivery message.

The workflow uses Playwright/Chromium and a virtual display, then commits the updated `state.json` back to the repository. It currently defines `workflow_dispatch` only; there is **no schedule/cron trigger**. Add a schedule only after the live fetch path and an appropriate polling interval have been validated.

## When an email is sent

The watcher compares each successful fetch with the previous state. An email is sent when one or more watches contain newly available options. The email contains the current list for affected watches and distinguishes new options from previously known options. When nothing newly available is detected, a normal availability digest is not sent.

If repeated fetch failures exceed the configured threshold (the workflow currently sets `REDBUS_FAIL_THRESHOLD=3`), the watcher can send a separate failure alert. A failed fetch must not erase the last successful state.

## Discovery and train implementation status

`discover.py` is an interactive capture tool intended to help inspect the network requests made by RedBus while searching. To continue train integration, capture an actual train search and inspect the saved request/response payloads. Verify at least:

- request URL, HTTP method, station identifiers and date format;
- any required headers, cookies/session requirements and request body;
- response fields for train identity, travel date, class, availability, fare, departure and arrival;
- behavior for unavailable classes and valid empty results.

Then implement the verified request in `redbus_watch/scraper_train.py` and add tests based on sanitized real fixtures. Until then, the current train adapter intentionally fails closed rather than generating false availability alerts.

## Tests

Install dependencies and run:

```bash
python -m pip install -r requirements.txt
python -m pytest tests/ -v
```

The tests cover configuration validation, state transitions, email rendering, orchestration and bus parsing against captured response fixtures. Unit tests do not prove that RedBus's live website/API will remain unchanged, so live fetch and email behavior should be verified from the target runtime.

## Operational notes

- RedBus's site and undocumented endpoints may change or temporarily reject requests.
- Use a conservative polling cadence and avoid excessive requests. This repository currently has no scheduled polling configured.
- Treat a fetch failure as **unknown availability**, not as zero available seats.
- The email's route link is best-effort and should be verified with a real RedBus browser session.
- Keep Resend credentials in GitHub Secrets or local environment variables, never in tracked files.

## License

See [LICENSE](LICENSE) if/when one is added to this repository.
