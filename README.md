# redbus-resend

A RedBus (bus + train) availability watcher, styled after your `bms-resend`
project: GitHub Actions cron → check availability → email a digest via
Resend when something newly opens up.

## Current status - read this first

| Component | Status |
|---|---|
| Config parsing (`REDBUS_WATCHES`) | Done, tested |
| State diffing (new-availability detection, false-alert prevention) | Done, tested |
| Email template (bus + train, styled like bms-resend) | Done, tested |
| Resend sending | Done |
| Orchestration (`main.py`) | Done, tested |
| GitHub Actions workflow | Done |
| **Bus scraping** (`scraper_bus.py`) | **Done - real endpoint confirmed, tested against your actual captured RedBus data** |
| **Train scraping** (`scraper_train.py`) | **Still a scaffold - endpoints not yet discovered** |

The bus scraper was finished by discovering RedBus's real internal API
(no public API exists, so this took a few discovery/fix iterations - see
git history / conversation for the full trail):

1. `GET /rpw/api/citySuggestion?search=<name>` resolves a city name to a
   numeric city ID (results pre-sorted by rank; take the first match).
2. `POST /rpw/api/searchResults?fromCity=<id>&toCity=<id>&DOJ=<DD-Mon-YYYY>&...`
   returns the actual bus inventory: operator, bus type, seats available,
   fares, departure/arrival times.

Both calls are made via an in-page `fetch()` inside a real (headless)
browser session, using the same cookies RedBus's own frontend would use -
this turned out to be far more reliable than passively intercepting
network responses, which failed silently in earlier attempts.

`tests/test_scraper_bus.py` runs the parsing logic against
`tests/fixtures/real_bus_search_response.json` - your **actual** captured
RedBus response, not synthetic data - so this is a genuine regression
test, not a guess.

**Train is not done yet.** RedBus's train-search endpoints are still
unknown. Same discovery process as bus, just needs one more round:

```bash
python discover.py --mode train --source Chennai --destination Bangalore --date 2026-08-20
```

This uses the interactive capture approach (you search manually, the
script listens to everything) since we don't know the endpoint pattern
yet. Send back the resulting `discovery_output/` and I'll finish
`scraper_train.py` the same way.

## Step 1: run the train discovery script (you, locally)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

python discover.py --mode train --source Chennai --destination Bangalore --date 2026-08-20
```

This opens a real (visible) Chromium window, you do the search manually
(or the script's best-effort auto-navigation may partially work), and it
saves every JSON API response RedBus's frontend makes into
`discovery_output/`. Send me that folder (zipped) and I'll wire the real
field names into `scraper_bus.py` / `scraper_train.py` in one focused pass
- at that point this becomes a fully working, fully tested end-to-end
system, not just the logic layer.

## Step 2: local test run (once scrapers are finished)

```bash
export REDBUS_WATCHES='[{"type":"bus","source":"Chennai","destination":"Bangalore","date":"2026-08-15"}]'
export RESEND_API_KEY=your_key
export RESEND_FROM=alerts@yourdomain.com
export RESEND_TO=you@example.com
python -m redbus_watch.main
```

## Step 3: GitHub Actions setup

Add these repo secrets (Settings → Secrets and variables → Actions):
- `REDBUS_WATCHES` - your JSON watch list
- `RESEND_API_KEY`
- `RESEND_FROM`
- `RESEND_TO`

The workflow (`.github/workflows/redbus-watch.yml`) runs every 15 minutes,
commits `state.json` back to the repo each run (same pattern as
bms-resend), and can be triggered manually via the Actions tab
("Run workflow" button) for testing.

**On polling frequency**: start at 15 minutes, not tighter. RedBus's bot
detection is unknown to both of us right now - better to find out it's
stable at a conservative interval than to get the runner's IP flagged on
day one.

## REDBUS_WATCHES format

```json
[
  {
    "type": "bus",
    "source": "Chennai",
    "destination": "Bangalore",
    "date": "2026-08-15",
    "label": "Chennai to Bangalore - Aug 15",
    "seat_type": "any",
    "operators": ["SRS Travels", "KPN"]
  },
  {
    "type": "bus",
    "source": "Chennai",
    "destination": "Coimbatore",
    "dates": ["2026-08-15", "2026-08-16", "2026-08-17"],
    "seat_type": "sleeper"
  },
  {
    "type": "train",
    "source": "MAS",
    "destination": "SBC",
    "date": "2026-08-20",
    "label": "Chennai to Bangalore Train - Aug 20",
    "classes": ["SL", "3A"]
  }
]
```

New fields (as of this update):
- **`dates`** (list, optional): watch multiple dates for one route without
  duplicating the whole block. Use this OR `date`, not both. Each date
  becomes its own separately-tracked watch.
- **`operators`** (bus only, list of strings, optional): watch for one
  particular bus/operator instead of "any bus on this route". Matches are
  case-insensitive substrings, e.g. `"SRS"` matches `"SRS Travels"`. Omit
  for "any operator counts."

Bus results in the email are now **sorted by price, low to high**.

## Known limitation: RedBus search link in the email

The email's "view route" link is built using the real numeric city IDs
RedBus's own internal API uses (confirmed correct for the API itself) -
this was fixed after an earlier version used city names and showed
"undefined" on RedBus's site. The link format for the human-facing search
*page* (as opposed to the API) is a best-effort match, not something I've
independently confirmed against a real browser session. If it still
doesn't land correctly, do one manual search on redbus.in and send me the
resulting address-bar URL and I'll lock in the exact format.

## Running the test suite

```bash
pip install -r requirements.txt
pytest tests/ -v
```

68 tests, all passing, covering:
- Config validation (missing fields, bad types, duplicates)
- Availability diffing (new item appears, 0→available transition,
  RAC/waitlist handling, **no re-alert on unchanged availability**,
  **failed fetch never wipes previous state** - direct regression test for
  the BMS false-alert bug class)
- Email rendering (bus + train sections, HTML escaping, chip thresholds)
- Full orchestration with fake fetchers (single combined digest across
  multiple watches, failure-threshold alerting)
- **Bus scraper parsing, tested against your real captured RedBus data**
  (city ID resolution, seat-type filtering, field mapping, price
  extraction) - not synthetic fixtures

## What's genuinely untested and why

The bus scraper's parsing logic is tested against your real captured data
(strong evidence it's correct), but the full live end-to-end run (browser
launch -> real network call -> real RedBus response, in one go) hasn't
been executed by me, because my sandbox cannot reach redbus.in at all.
Run it locally once (`python -m redbus_watch.main` with a bus-only
`REDBUS_WATCHES`) to confirm the live path works exactly like the
discovery calls did.

The train scraper is still an unfinished scaffold - not tested at all
yet, pending its own discovery round.
