# RedBus Availability Watcher

A Python watcher for **RedBus bus availability**. It checks configured bus routes, compares each successful fetch with saved state, and sends a single HTML email digest through [Resend](https://resend.com) when newly available options are detected.

The project runs as a **manually triggered GitHub Actions workflow** or can be run locally. It is not currently configured with a scheduled cron trigger.

## Features

- Monitor multiple bus routes and travel dates through `REDBUS_WATCHES`.
- Filter by seat type (seater, sleeper, AC or non-AC) and bus operator.
- Track available seats, operator, bus type, fares and departure/arrival times.
- Detect newly available options by comparing successful fetches with the previous state.
- Send a combined HTML email digest through Resend when new availability is found.
- Track consecutive fetch failures and send a separate failure alert after the configured threshold.
- Persist state in `state.json` across GitHub Actions runs.

## How it works

1. Read and validate the JSON configuration in `REDBUS_WATCHES`.
2. Load the last-known availability snapshot from `state.json`.
3. Resolve the source and destination city identifiers and fetch bus-search results using a browser session.
4. Normalize the bus inventory and apply seat-type and operator filters.
5. Compare the latest successful results with the saved snapshot.
6. If new availability is detected, create a combined email digest containing the current options for affected routes and mark newly available options.
7. Record fetch failures without erasing the last successful snapshot. Send a separate failure alert when consecutive failures reach the configured threshold.
8. Save the updated state. The GitHub Actions workflow commits `state.json` to the repository for use in later runs.

## Repository structure

```text
.
├── .github/
│   └── workflows/
│       └── redbus-watch.yml       # Manual GitHub Actions workflow
├── redbus_watch/
│   ├── config.py                  # Watch-list parsing and validation
│   ├── main.py                    # Orchestration and change detection
│   ├── notifier.py                # Resend email delivery
│   ├── email_template.py          # HTML digest and failure-alert templates
│   ├── scraper_bus.py             # Bus search adapter
│   └── state.py                   # Persistent snapshots and failure tracking
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
- A [Resend](https://resend.com) account and sender address for email alerts
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

## Configure bus watches

Set the repository variable `REDBUS_WATCHES` in **Settings → Secrets and variables → Actions → Variables**. Its value must be a JSON array.

Example:

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
  },
  {
    "type": "bus",
    "source": "Chennai",
    "destination": "Bangalore",
    "date": "2026-12-15",
    "seat_type": "ac"
  }
]
```

Use either `date` for one date or `dates` for a list of dates, not both. Each date is tracked as a separate watch.

| Field | Required | Purpose |
|---|---|---|
| `type` | Yes | Must be `"bus"` |
| `source` | Yes | Origin city name |
| `destination` | Yes | Destination city name |
| `date` | One of `date`/`dates` | Travel date in `YYYY-MM-DD` format |
| `dates` | One of `date`/`dates` | List of travel dates in `YYYY-MM-DD` format |
| `label` | No | Human-readable label included in notification emails |
| `seat_type` | No | `any`, `seater`, `sleeper`, `ac` or `non_ac`; defaults to `any` |
| `operators` | No | Operator-name substrings to match, case-insensitively; omitted means any operator |

Duplicate watches that resolve to the same watch identifier are rejected during configuration validation.

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

1. Add the `REDBUS_WATCHES` repository variable under **Settings → Secrets and variables → Actions → Variables**.
2. Add these repository secrets under **Settings → Secrets and variables → Actions → Secrets**:
   - `RESEND_API_KEY`
   - `RESEND_FROM`
   - `RESEND_TO`
3. Open **Actions → RedBus Availability Watcher → Run workflow** and run it on `main`.
4. Review the workflow logs for the run summary and email-delivery status.

The workflow installs Python dependencies and Playwright/Chromium, starts a virtual display, runs the watcher, and commits the updated `state.json` file. It currently defines `workflow_dispatch` only; **no schedule/cron trigger is configured**.

## When emails are sent

The watcher compares each successful fetch against the last saved state. When newly available bus options are detected, it sends one combined digest email containing the current options for affected watches and highlighting newly available options. No normal availability email is sent when nothing new is detected.

If a fetch repeatedly fails, a separate failure alert can be sent after the consecutive-failure threshold is reached. The current GitHub Actions workflow sets `REDBUS_FAIL_THRESHOLD=3`.

## Testing

Install the dependencies and run:

```bash
python -m pytest tests/ -v
```

The tests cover configuration validation, state transitions, email rendering, orchestration and bus-response parsing against captured fixtures. Unit tests cannot guarantee that RedBus's live website or undocumented API will remain unchanged, so verify live behavior from the intended runtime environment.

## Operational notes

- RedBus's website and internal endpoints may change or temporarily reject requests.
- Use a conservative polling cadence and avoid excessive requests. This repository currently has no scheduled polling configured.
- Treat a fetch failure as **unknown availability**, not as zero available seats.
- Keep Resend credentials in GitHub Secrets or local environment variables, never in tracked files.
- Confirm the sender address is permitted by your Resend account before relying on notifications.

## License

See [LICENSE](LICENSE) if/when a license file is added to this repository.
