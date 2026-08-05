"""
RedBus train-search scraper. Same status/caveats as scraper_bus.py -
scaffold is real and ready, field-mapping is a placeholder pending your
discover.py output for a train search.
"""

from typing import Dict, List, Optional
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError


class ScrapeError(RuntimeError):
    pass


def _looks_like_train_search_response(url: str, content_type: str) -> bool:
    """TODO(after discovery): replace with the exact train-search endpoint path."""
    return "json" in content_type.lower() and any(
        kw in url.lower() for kw in ("train", "search", "avail", "schedule")
    )


def _parse_train_search_response(body: dict, classes_filter: Optional[List[str]]) -> Dict[str, dict]:
    """
    TODO(after discovery): map real JSON shape into:
        { "<train_no>_<class>": {
              "train_name": str, "train_no": str, "class": str,
              "status_text": str,  # e.g. "AVAILABLE", "RAC 4", "WL 12"
              "departure_time": str
          }, ... }
    If classes_filter is provided, only include those classes.
    """
    raise NotImplementedError(
        "Train search response parsing not yet implemented - needs real "
        "discovery_output data to map field names correctly."
    )


def fetch_train_availability(
    source: str, destination: str, date: str, classes_filter: Optional[List[str]] = None
) -> Dict[str, dict]:
    """
    Same pattern as fetch_bus_availability: headless browser, intercept the
    search response, parse into snapshot dicts. Raises ScrapeError on
    failure so the caller can handle it via the failure-alert path rather
    than crashing.
    """
    captured = {}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            def handle_response(response):
                try:
                    ct = response.headers.get("content-type", "")
                    if _looks_like_train_search_response(response.url, ct):
                        captured["body"] = response.json()
                except Exception:
                    pass

            page.on("response", handle_response)

            # TODO(after discovery): replace with the real train search URL pattern.
            search_url = (
                f"https://www.redbus.in/trains/search?fromStation={source}"
                f"&toStation={destination}&date={date}"
            )
            page.goto(search_url, wait_until="networkidle", timeout=45000)

            browser.close()
    except PWTimeoutError as e:
        raise ScrapeError(f"Timed out loading RedBus train search for {source}->{destination}: {e}") from e
    except Exception as e:
        raise ScrapeError(f"Unexpected error scraping RedBus train search: {e}") from e

    if "body" not in captured:
        raise ScrapeError(
            f"No matching search response captured for train {source}->{destination} on {date}."
        )

    return _parse_train_search_response(captured["body"], classes_filter)
