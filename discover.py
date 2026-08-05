"""
DISCOVERY SCRIPT - run this locally in your WSL, not in any sandbox.

BUS MODE (--mode bus): uses a DETERMINISTIC in-page fetch approach.
From your last run, we already confirmed the real endpoints RedBus's own
frontend uses:
  1. GET  https://www.redbus.in/rpw/api/citySuggestion?search=<name>&limit=10&routeDetection=false
  2. POST https://www.redbus.in/rpw/api/searchResults?fromCity=<id>&toCity=<id>&DOJ=<DD-Mon-YYYY>&...

Rather than passively listening for these calls to fly by (which failed
silently last time - see note below), this mode navigates to redbus.in
first to pick up cookies/session, then calls these two endpoints directly
via `page.evaluate(...)` - i.e. the fetch happens INSIDE the browser page
using its real session, and the JSON comes back to us as a normal Python
return value. No race condition, no silent parse failures.

TRAIN MODE (--mode train): we don't know the real endpoints yet, so this
still uses the interactive "you search manually, we listen to everything"
approach from before - but now with a bug fixed. Last time, when a
response's JSON parse failed, we silently swallowed the error and never
knew why. This version logs the actual exception for every response that
looked promising (JSON content-type or plausible URL) but failed to parse,
so if it still comes up empty, we'll know exactly why instead of guessing
again.

HOW TO RUN:
    source venv/bin/activate
    pip install -r requirements.txt
    python -m playwright install chromium

    python discover.py --mode bus   --source Chennai --destination Bangalore --date 2026-08-15
    python discover.py --mode train --source Chennai --destination Bangalore --date 2026-08-15

WHAT TO DO WITH THE OUTPUT:
    Zip up discovery_output/ and send it back.
"""

import argparse
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urlparse, quote

from playwright.sync_api import sync_playwright


OUTPUT_DIR = "discovery_output"

NOISE_DOMAINS = [
    "google-analytics", "googletagmanager", "doubleclick", "facebook.com",
    "fbcdn", "hotjar", "clarity.ms", "segment.io", "mixpanel", "amplitude",
    "sentry.io", "newrelic", "cloudflareinsights", "criteo", "adnxs",
    "analytics.google.com", "ultron-api.redbus.com",
]


def is_noise(url: str) -> bool:
    lower_url = url.lower()
    return any(d in lower_url for d in NOISE_DOMAINS)


def sanitize_filename(url: str) -> str:
    parsed = urlparse(url)
    name = f"{parsed.netloc}{parsed.path}"
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    return name[:120]


def _save_json(name: str, payload: dict):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fpath = os.path.join(OUTPUT_DIR, name)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[saved] {fpath}")


def _date_to_redbus_format(date_str: str) -> str:
    """Converts YYYY-MM-DD -> DD-Mon-YYYY (e.g. 2026-08-15 -> 15-Aug-2026),
    matching the exact format observed in the real searchResults call."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%d-%b-%Y")


def run_direct_bus_fetch(source: str, destination: str, date: str):
    """
    Deterministic path: navigate to redbus.in for cookies/session, then call
    the two confirmed real endpoints directly via in-page fetch and save
    whatever comes back - success or a real HTTP error - so we can see it.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    redbus_date = _date_to_redbus_format(date)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        print("\nOpening redbus.in to establish session/cookies ...")
        page.goto("https://www.redbus.in", wait_until="networkidle", timeout=60000)
        time.sleep(2)

        def in_page_fetch(url: str, method: str = "GET") -> dict:
            script = """
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
                            contentType: resp.headers.get('content-type'),
                            rawTextLength: text.length,
                            rawTextSnippet: text.slice(0, 500),
                            parsed: parsed,
                            parseError: parseError,
                        };
                    } catch (e) {
                        return { fetchError: String(e) };
                    }
                }
            """
            return page.evaluate(script, {"url": url, "method": method})

        source_url = f"https://www.redbus.in/rpw/api/citySuggestion?search={quote(source)}&limit=10&routeDetection=false"
        print(f"\nFetching source city suggestions: {source_url}")
        source_result = in_page_fetch(source_url)
        _save_json("1_source_city_suggestion.json", {"request_url": source_url, "result": source_result})

        dest_url = f"https://www.redbus.in/rpw/api/citySuggestion?search={quote(destination)}&limit=10&routeDetection=false"
        print(f"Fetching destination city suggestions: {dest_url}")
        dest_result = in_page_fetch(dest_url)
        _save_json("2_dest_city_suggestion.json", {"request_url": dest_url, "result": dest_result})

        def find_city_id(result: dict):
            """
            Real shape confirmed from your last run:
              parsed.response.docs -> list, sorted by rank desc.
              First doc's "ID" field is the city ID we want.
            """
            parsed = result.get("parsed")
            if not parsed:
                return None, None
            docs = (parsed.get("response") or {}).get("docs") or []
            if not docs:
                return None, None
            first = docs[0]
            return first.get("ID"), first.get("Name")

        source_city_id, source_city_name = find_city_id(source_result)
        dest_city_id, dest_city_name = find_city_id(dest_result)

        print(f"\nAuto-detected source: ID={source_city_id} ('{source_city_name}'), "
              f"destination: ID={dest_city_id} ('{dest_city_name}')")
        if source_city_id is None or dest_city_id is None:
            print(
                "\n[warn] Could not auto-detect city IDs from the response shape - "
                "that's fine, open 1_source_city_suggestion.json and "
                "2_dest_city_suggestion.json yourself and send them back; I'll read "
                "the real field names directly. Skipping the searchResults call for now."
            )
        else:
            search_url = (
                f"https://www.redbus.in/rpw/api/searchResults?fromCity={source_city_id}"
                f"&toCity={dest_city_id}&DOJ={redbus_date}&limit=10&offset=0&meta=true"
                f"&groupId=0&sectionId=0&sort=0&sortOrder=0&from=initialLoad"
                f"&getUuid=true&bT=1&clearLMBFilter=undefined&isFilterApplied=false"
            )
            print(f"\nFetching bus search results: {search_url}")
            search_result = in_page_fetch(search_url, method="POST")
            _save_json("3_bus_search_results.json", {
                "request_url": search_url,
                "source_city": {"id": source_city_id, "name": source_city_name},
                "dest_city": {"id": dest_city_id, "name": dest_city_name},
                "result": search_result,
            })

        print("\nLeaving the browser open for 15 seconds in case you want to look around manually too.")
        time.sleep(15)
        browser.close()

    print(f"\nDone. Check ./{OUTPUT_DIR}/ for 1_source_city_suggestion.json, "
          f"2_dest_city_suggestion.json, and (if IDs resolved) 3_bus_search_results.json.")
    print("Zip up discovery_output/ and send it back, even if some steps show errors -"
          " the error details themselves are useful.")


def run_interactive_discovery(mode: str, source: str, destination: str, date: str):
    """Interactive fallback for train (endpoints unknown) - now with real error logging."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    captured_count = 0
    log_path = os.path.join(OUTPUT_DIR, "all_traffic_log.txt")
    log_lines = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=150)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        def handle_request(request):
            if request.resource_type in ("document", "xhr", "fetch"):
                log_lines.append(f"[REQUEST] {request.method} {request.resource_type} {request.url}")

        def handle_response(response):
            nonlocal captured_count
            content_type = response.headers.get("content-type", "")
            content_length = response.headers.get("content-length", "?")
            log_lines.append(
                f"[RESPONSE] {response.status} ct={content_type or '(none)'} "
                f"len={content_length} {response.url}"
            )
            if is_noise(response.url):
                return

            try:
                body = response.json()
            except Exception as e:
                if "json" in content_type.lower() or "/api/" in response.url or "/rpw/" in response.url:
                    log_lines.append(f"    [PARSE FAILED] {type(e).__name__}: {e}")
                return

            try:
                body_size = len(json.dumps(body))
            except Exception:
                body_size = 0
            if body_size < 50:
                return

            captured_count += 1
            ts = datetime.now().strftime("%H%M%S_%f")
            fname = f"{ts}__{sanitize_filename(response.url)}.json"
            fpath = os.path.join(OUTPUT_DIR, fname)
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump({"url": response.url, "status": response.status, "body": body}, f, indent=2)
                print(f"[captured] {response.url}  ->  {fname}")
            except Exception as e:
                print(f"[warn] failed to save response from {response.url}: {e}")

        page.on("request", handle_request)
        page.on("response", handle_response)

        def handle_new_page(new_page):
            print(f"[info] new tab/page opened: {new_page.url}")
            new_page.on("request", handle_request)
            new_page.on("response", handle_response)

        context.on("page", handle_new_page)

        print("\nOpening redbus.in ...")
        page.goto("https://www.redbus.in", wait_until="domcontentloaded", timeout=60000)

        print(
            f"\nBrowser window is open. Please manually search for a TRAIN:\n"
            f"  From: {source}\n"
            f"  To:   {destination}\n"
            f"  Date: {date}\n\n"
            f"Use RedBus's train search section/tab. Perform the search, wait for\n"
            f"results to fully load.\n"
        )
        input("Press ENTER here once you've finished searching and results are visible... ")

        time.sleep(5)
        browser.close()

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

    print(f"\nDone. Captured {captured_count} JSON response(s) into ./{OUTPUT_DIR}/")
    print(f"Full log (including any parse failures) written to ./{OUTPUT_DIR}/all_traffic_log.txt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Capture RedBus's internal API responses for a search.")
    parser.add_argument("--mode", choices=["bus", "train"], required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    if args.mode == "bus":
        run_direct_bus_fetch(args.source, args.destination, args.date)
    else:
        run_interactive_discovery(args.mode, args.source, args.destination, args.date)
