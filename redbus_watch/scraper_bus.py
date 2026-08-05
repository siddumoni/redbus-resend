"""
RedBus bus-search scraper - REAL implementation.

Confirmed against your actual discovery_output captures (saved permanently
as tests/fixtures/real_*.json so the parsing logic has a genuine
regression test, not a guessed one):

  1. GET  https://www.redbus.in/rpw/api/citySuggestion?search=<name>&limit=10&routeDetection=false
     -> {"response": {"docs": [{"ID": 123, "Name": "Chennai", ...}, ...]}}
     Results are pre-sorted by rank desc, so docs[0] is the right city.

  2. POST https://www.redbus.in/rpw/api/searchResults?fromCity=<id>&toCity=<id>
          &DOJ=<DD-Mon-YYYY>&limit=100&offset=0&meta=true&groupId=0&sectionId=0
          &sort=0&sortOrder=0&from=initialLoad&getUuid=true&bT=1
          &clearLMBFilter=undefined&isFilterApplied=false
     Body: '{}' with Content-Type: application/json
     -> {"success": true, "data": {"metaData": {"totalCount": N},
          "inventories": [ {...one bus...}, ... ]}}

     Relevant per-bus fields (confirmed from real data):
       travelsName, busType, routeId, serviceId, operatorId,
       departureTime ("YYYY-MM-DD HH:MM:SS"), arrivalTime,
       availableSeats (int), totalSeats (int), fareList (list of int),
       isAc (bool), isSleeper (bool)

Both calls are made via page.evaluate (in-page fetch), using the same
cookies/session as a real browser visit to redbus.in - this was the
approach that actually worked in testing, vs. passive response
interception which turned out to be unreliable.
"""

import os
import re
import socket
import time
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# --disable-blink-features=AutomationControlled: hides the most common
# headless-detection signal (navigator.webdriver).
# --no-sandbox / --disable-dev-shm-usage: standard fixes for headless
# Chromium resource/permission quirks in WSL and containerized environments.
# --disable-features=AsyncDns: Chromium has its own built-in DNS resolver
# that can fail (net::ERR_NAME_NOT_RESOLVED) inside WSL2 even when the
# system's actual DNS resolution works perfectly (confirmed via ping/curl/
# nslookup) - WSL2's synthetic DNS proxy (the 10.255.255.254-style address)
# doesn't always play well with Chromium's internal resolver. This flag
# forces Chromium to use the system resolver instead of its own.
BROWSER_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-features=AsyncDns",
]

IN_PAGE_FETCH_SCRIPT = """
    async ({ url, method }) => {
        try {
            const opts = { method, credentials: 'include' };
            if (method === 'POST') {
                opts.headers = { 'Content-Type': 'application/json' };
                opts.body = '{}';
            }
            const resp = await fetch(url, opts);
            const text = await resp.text();
            let parsed = null;
            let parseError = null;
            try { parsed = JSON.parse(text); } catch (e) { parseError = String(e); }
            return {
                ok: resp.ok,
                status: resp.status,
                rawTextSnippet: text.slice(0, 300),
                parsed: parsed,
                parseError: parseError,
            };
        } catch (e) {
            return { fetchError: String(e) };
        }
    }
"""


class ScrapeError(RuntimeError):
    pass


def _date_to_redbus_format(date_str: str) -> str:
    """YYYY-MM-DD -> DD-Mon-YYYY, e.g. 2026-08-15 -> 15-Aug-2026."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%d-%b-%Y")


def _extract_city_id(city_suggestion_response: dict, city_query: str) -> int:
    """
    Parses a citySuggestion response and returns the top-ranked city ID.
    Raises ScrapeError with a clear message if nothing usable is found -
    this is a common real failure mode (typo'd city name, city not served).
    """
    if not city_suggestion_response.get("ok"):
        raise ScrapeError(
            f"citySuggestion lookup for '{city_query}' failed: "
            f"HTTP {city_suggestion_response.get('status')}"
        )
    parsed = city_suggestion_response.get("parsed")
    if not parsed:
        raise ScrapeError(
            f"citySuggestion lookup for '{city_query}' returned no parseable JSON "
            f"(parseError: {city_suggestion_response.get('parseError')})"
        )
    docs = (parsed.get("response") or {}).get("docs") or []
    if not docs:
        raise ScrapeError(
            f"citySuggestion lookup for '{city_query}' returned zero matches - "
            f"check spelling or whether RedBus serves this city"
        )
    city_id = docs[0].get("ID")
    if city_id is None:
        raise ScrapeError(f"citySuggestion top match for '{city_query}' has no 'ID' field: {docs[0]}")
    return city_id


def _passes_seat_type_filter(inv: dict, seat_type: str) -> bool:
    if seat_type == "any":
        return True
    if seat_type == "sleeper":
        return bool(inv.get("isSleeper"))
    if seat_type == "seater":
        return not inv.get("isSleeper")
    if seat_type == "ac":
        return bool(inv.get("isAc"))
    if seat_type == "non_ac":
        return not inv.get("isAc")
    return True  # unknown filter value - fail open rather than hide everything


def _normalize_operator_text(text: str) -> str:
    """Strips everything except letters/digits and lowercases, so operator
    matching isn't broken by spacing/punctuation differences between how
    you write the name and how RedBus's data has it stored
    (e.g. "SNB" should match "S N B Travels" or "S.N.B. Travels")."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _passes_operator_filter(inv: dict, operators: Optional[List[str]]) -> bool:
    """
    If operators is None/empty, everything passes (no filter). Otherwise
    only buses whose travelsName contains one of the given substrings
    (case-insensitive, punctuation/spacing-insensitive) pass - lets you
    watch for one specific operator/bus rather than "any bus on this route".
    """
    if not operators:
        return True
    travels_name = _normalize_operator_text(inv.get("travelsName") or "")
    return any(_normalize_operator_text(op) in travels_name for op in operators)


def parse_bus_search_response(
    search_response: dict,
    seat_type: str = "any",
    operators: Optional[List[str]] = None,
    city_ids: Optional[Dict[str, int]] = None,
) -> Dict[str, dict]:
    """
    Pure function: takes the already-fetched search response dict (same
    shape as tests/fixtures/real_bus_search_response.json -> result key)
    and returns our standard snapshot dict format, keyed by a stable
    per-bus identifier (serviceId, falling back to routeId).

    Results are sorted by price ascending (buses with no fare data sort
    last) - dict insertion order is preserved in Python 3.7+, and
    state.py/email_template.py both iterate snapshots in the order
    they're given, so sorting here is what actually controls email order.

    If city_ids is given (e.g. {"source": 123, "dest": 122}), a
    "__meta__" entry is included with those IDs so the caller can build a
    working RedBus link. Keys starting with "__" are ignored by the
    diffing/state logic (see state.py) - they're not real bookable items.

    Kept separate from the network/browser code so it's fully unit
    testable against a real fixture with no Playwright involved.
    """
    if not search_response.get("ok"):
        raise ScrapeError(f"searchResults call failed: HTTP {search_response.get('status')}")

    parsed = search_response.get("parsed")
    if not parsed:
        raise ScrapeError(
            f"searchResults returned no parseable JSON (parseError: {search_response.get('parseError')})"
        )

    if not parsed.get("success"):
        raise ScrapeError(f"searchResults responded but success=false: {parsed}")

    inventories = (parsed.get("data") or {}).get("inventories") or []

    rows = []  # (sort_price, key, snapshot) so we can sort before building the final dict
    for inv in inventories:
        if not _passes_seat_type_filter(inv, seat_type):
            continue
        if not _passes_operator_filter(inv, operators):
            continue

        departure_raw = inv.get("departureTime", "")  # "2026-08-15 23:00:00"
        arrival_raw = inv.get("arrivalTime", "")

        # Composite key (serviceId/routeId + departure time) rather than
        # serviceId alone - defensive against the same serviceId being
        # reused across different departure times, which would otherwise
        # silently collapse two distinct buses into one tracked entry.
        base_id = inv.get("serviceId") or inv.get("routeId")
        if not base_id:
            continue  # can't reliably track this one across runs, skip it
        key = f"{base_id}_{departure_raw}"

        fare_list = inv.get("fareList") or []
        price = min(fare_list) if fare_list else None
        sort_price = price if price is not None else float("inf")  # no-fare buses sort last

        try:
            departure_dt = datetime.strptime(departure_raw, "%Y-%m-%d %H:%M:%S")
            departure_display = departure_dt.strftime("%H:%M")
        except ValueError:
            departure_dt = None
            departure_display = departure_raw

        try:
            arrival_dt = datetime.strptime(arrival_raw, "%Y-%m-%d %H:%M:%S")
            arrival_display = arrival_dt.strftime("%H:%M")
            if departure_dt is not None:
                day_diff = (arrival_dt.date() - departure_dt.date()).days
                if day_diff > 0:
                    arrival_display += f" (+{day_diff}d)"
        except ValueError:
            arrival_display = arrival_raw

        rows.append((sort_price, key, {
            "operator": inv.get("travelsName", "Unknown operator"),
            "bus_type": inv.get("busType", ""),
            "available": inv.get("availableSeats", 0),
            "price": price,
            "departure_time": departure_display,
            "arrival_time": arrival_display,
        }))

    rows.sort(key=lambda r: r[0])

    snapshots: Dict[str, dict] = {key: snap for _, key, snap in rows}

    if city_ids:
        snapshots["__meta__"] = {"city_ids": city_ids}

    return snapshots


def _merge_search_pages(pages: List[dict]) -> dict:
    """
    Combines multiple single-page search_response dicts (same shape as one
    in_page_fetch result) into one synthetic response with all inventories
    concatenated, so parse_bus_search_response can be called once on the
    combined data - keeping that function's tested single-page contract
    unchanged.
    """
    if not pages:
        return {"ok": False, "status": 0, "parseError": "No pages fetched"}

    first = pages[0]
    if not first.get("ok") or not first.get("parsed"):
        return first  # surface the first page's failure as-is

    all_inventories = []
    for page in pages:
        if page.get("ok") and page.get("parsed") and page["parsed"].get("success"):
            all_inventories.extend((page["parsed"].get("data") or {}).get("inventories") or [])

    merged_parsed = dict(first["parsed"])
    merged_parsed["data"] = dict(merged_parsed.get("data") or {})
    merged_parsed["data"]["inventories"] = all_inventories

    merged = dict(first)
    merged["parsed"] = merged_parsed
    return merged


def fetch_bus_availability(
    source: str, destination: str, date: str, seat_type: str = "any", operators: Optional[List[str]] = None
) -> Dict[str, dict]:
    """
    Full flow: launch headless browser, resolve source/destination city
    names to IDs, call the search endpoint (paginating through ALL results,
    not just the first page - see note below), parse into snapshot dicts.
    Raises ScrapeError on any failure so the caller can route it through
    the consecutive-failure alerting instead of crashing the whole run.

    IMPORTANT: confirmed against real data that RedBus's searchResults API
    ignores the "limit" query param we send (we requested limit=100 but it
    returned only 10 buses per call, out of a real totalCount of 215).
    Without pagination, watches would silently miss any bus ranked beyond
    the first ~10 results - exactly the bug that caused a specific
    operator to be missed entirely. PAGE_SIZE_CAP bounds how many pages we
    fetch even for extremely long routes, so a busy route can't turn one
    watcher run into hundreds of requests.
    """
    redbus_date = _date_to_redbus_format(date)
    PAGE_SIZE_CAP = 30  # safety cap: up to ~300 buses (30 pages x ~10/page) per watch per run

    launch_args = list(BROWSER_LAUNCH_ARGS)

    # --disable-http2 fixed a real net::ERR_HTTP2_PROTOCOL_ERROR seen on
    # one WSL2 setup, but it's opt-in (not default) because forcing
    # HTTP/1.1 makes the browser look LESS like a real Chrome session
    # (real Chrome always negotiates HTTP/2) - on a site actively running
    # Akamai Bot Manager fingerprinting (confirmed via _abck/bm_sz cookies
    # in response headers), that's a bad trade almost everywhere except
    # the specific WSL setup that needed it. Set REDBUS_DISABLE_HTTP2=true
    # only if you hit that exact error again.
    if os.environ.get("REDBUS_DISABLE_HTTP2", "").strip().lower() == "true":
        launch_args.append("--disable-http2")

    try:
        # Belt-and-suspenders on top of --disable-features=AsyncDns: resolve
        # the hostname ourselves via Python's socket module (confirmed
        # working via ping/curl/nslookup even when Chromium's own resolver
        # fails inside WSL2) and hand Chromium the IP directly. This fully
        # bypasses whichever internal Chromium DNS mechanism is failing.
        resolved_ip = socket.gethostbyname("www.redbus.in")
        launch_args.append(f"--host-resolver-rules=MAP www.redbus.in {resolved_ip}")
    except socket.gaierror:
        pass  # if even Python can't resolve it, let Chromium try normally and surface its own error

    try:
        with sync_playwright() as p:
            headless = os.environ.get("REDBUS_HEADLESS", "true").strip().lower() != "false"
            browser = p.chromium.launch(headless=headless, args=launch_args)
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()

            # "networkidle" is a well-known flaky wait condition on sites
            # with background analytics/heartbeat requests - the page can
            # be fully usable while Playwright still waits forever for zero
            # network activity. Try it briefly first (it's the most
            # "settled" state if it works), then fall back to the more
            # reliable domcontentloaded + a fixed settle pause.
            navigated = False
            try:
                page.goto("https://www.redbus.in", wait_until="networkidle", timeout=20000)
                navigated = True
            except PWTimeoutError:
                pass  # fall through to the more reliable path below

            if not navigated:
                last_error = None
                for attempt in range(3):
                    try:
                        if attempt > 0:
                            time.sleep(3 * attempt)  # backoff: 0s, 3s, 6s between attempts
                        page.goto("https://www.redbus.in", wait_until="domcontentloaded", timeout=30000)
                        time.sleep(3)  # let RedBus's JS finish initializing (cookies, session, etc.)
                        navigated = True
                        last_error = None
                        break
                    except Exception as e:
                        last_error = e
                if last_error is not None:
                    raise ScrapeError(
                        f"Failed to load redbus.in after {attempt + 1} attempts: {last_error}"
                    ) from last_error

            def in_page_fetch(url: str, method: str = "GET") -> dict:
                return page.evaluate(IN_PAGE_FETCH_SCRIPT, {"url": url, "method": method})

            source_url = f"https://www.redbus.in/rpw/api/citySuggestion?search={quote(source)}&limit=10&routeDetection=false"
            dest_url = f"https://www.redbus.in/rpw/api/citySuggestion?search={quote(destination)}&limit=10&routeDetection=false"

            source_city_id = _extract_city_id(in_page_fetch(source_url), source)
            dest_city_id = _extract_city_id(in_page_fetch(dest_url), destination)

            def build_search_url(offset: int) -> str:
                return (
                    f"https://www.redbus.in/rpw/api/searchResults?fromCity={source_city_id}"
                    f"&toCity={dest_city_id}&DOJ={redbus_date}&limit=100&offset={offset}&meta=true"
                    f"&groupId=0&sectionId=0&sort=0&sortOrder=0&from=initialLoad"
                    f"&getUuid=true&bT=1&clearLMBFilter=undefined&isFilterApplied=false"
                )

            pages = []
            offset = 0
            total_count = None
            for page_num in range(PAGE_SIZE_CAP):
                page_response = in_page_fetch(build_search_url(offset), method="POST")
                pages.append(page_response)

                parsed = page_response.get("parsed")
                if not page_response.get("ok") or not parsed or not parsed.get("success"):
                    break  # let _merge_search_pages/parse_bus_search_response surface this failure

                page_inventories = (parsed.get("data") or {}).get("inventories") or []
                if total_count is None:
                    total_count = (parsed.get("data") or {}).get("metaData", {}).get("totalCount")

                offset += len(page_inventories)

                # Stop once we've covered everything the API says exists,
                # or once a page comes back empty (belt-and-suspenders
                # against an infinite loop if totalCount is ever wrong).
                if len(page_inventories) == 0:
                    break
                if total_count is not None and offset >= total_count:
                    break

            search_response = _merge_search_pages(pages)

            browser.close()
    except PWTimeoutError as e:
        raise ScrapeError(f"Timed out loading RedBus for {source}->{destination}: {e}") from e
    except ScrapeError:
        raise
    except Exception as e:
        raise ScrapeError(f"Unexpected error scraping RedBus bus search: {e}") from e

    return parse_bus_search_response(
        search_response,
        seat_type,
        operators=operators,
        city_ids={"source": source_city_id, "dest": dest_city_id},
    )
