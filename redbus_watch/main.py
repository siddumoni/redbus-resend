"""
Orchestrates one run of the watcher:
  1. load watches from REDBUS_WATCHES
  2. load last-known state from state.json
  3. for each watch, fetch fresh availability (bus or train)
  4. compare against last-known state to see what's newly available
  5. if anything is newly available, send ONE combined digest email
     showing the FULL current list of available options for that watch,
     with newly-available ones marked "NEW" - not just the delta. This
     avoids spam (no email if nothing changed) while still giving the
     complete current picture whenever something does change.
  6. handle fetch failures with a consecutive-failure threshold alert
  7. save updated state.json

`run_once` takes the fetch/send functions as parameters (dependency
injection) specifically so this core logic can be unit-tested with fake
fetchers - no real RedBus or Resend calls needed to verify the
orchestration, diffing, and email-triggering logic is correct.

`main()` is the thin real-world wiring: real scrapers, real Resend sender,
real env vars, real state.json on disk. That's the only part that can't be
tested from a sandbox without live access.
"""

import os
import sys
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from redbus_watch.config import Watch, ConfigError, load_watches_from_env
from redbus_watch.state import (
    load_state,
    save_state,
    get_full_availability_with_new_flags,
    update_state_success,
    record_fetch_failure,
    mark_alerted,
)
from redbus_watch.email_template import build_digest_email, build_failure_alert_email

FAIL_THRESHOLD_DEFAULT = 3


def _watch_url(watch: Watch, city_ids: Optional[dict] = None) -> str:
    if watch.type == "bus":
        if city_ids and city_ids.get("source") and city_ids.get("dest"):
            # Uses the real internal API's own param convention (numeric
            # city IDs, not names) - confirmed correct for the search API
            # itself. Best-effort for the human-facing search page; if
            # RedBus's frontend expects a different URL shape, this may
            # still need one more real-world confirmation.
            return (
                f"https://www.redbus.in/search?fromCity={city_ids['source']}"
                f"&toCity={city_ids['dest']}&onward={watch.date}"
            )
        # Fallback if city IDs aren't available for some reason (e.g. a
        # fetcher that doesn't resolve them) - at least links to the site.
        return "https://www.redbus.in"
    return f"https://www.redbus.in/trains/search?fromStation={watch.source}&toStation={watch.destination}&date={watch.date}"


def run_once(
    watches: List[Watch],
    states: Dict,
    fetch_bus_fn: Callable[[str, str, str, str, Optional[List[str]]], Dict[str, dict]],
    fetch_train_fn: Callable[[str, str, str, Optional[List[str]]], Dict[str, dict]],
    send_email_fn: Callable[[str, str], None],
    fail_threshold: int = FAIL_THRESHOLD_DEFAULT,
    now_iso: Optional[str] = None,
) -> dict:
    """
    Returns a summary dict for logging/testing purposes:
      {
        "emailed_watches": [...watch_ids...],
        "failed_watches": [...watch_ids...],
        "failure_alerts_sent": [...watch_ids...],
        "errors": {watch_id: error_message},
      }
    """
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    summary = {
        "emailed_watches": [],
        "failed_watches": [],
        "failure_alerts_sent": [],
        "errors": {},
    }

    digest_sections = []

    for watch in watches:
        wid = watch.watch_id()
        try:
            if watch.type == "bus":
                fresh = fetch_bus_fn(watch.source, watch.destination, watch.date, watch.seat_type, watch.operators)
            else:
                fresh = fetch_train_fn(watch.source, watch.destination, watch.date, watch.classes)
        except Exception as e:
            summary["failed_watches"].append(wid)
            summary["errors"][wid] = str(e)
            failures = record_fetch_failure(wid, states)
            if failures >= fail_threshold:
                try:
                    send_email_fn(
                        f"[RedBus Watcher] Repeated failures: {watch.label}",
                        build_failure_alert_email(watch.label, failures, fail_threshold),
                    )
                    summary["failure_alerts_sent"].append(wid)
                    mark_alerted(wid, states, now_iso)
                except Exception as send_err:
                    summary["errors"][wid] = f"{e}; ALSO failed to send failure alert: {send_err}"
            continue

        # Keys starting with "__" carry metadata (e.g. resolved city IDs
        # for building a working RedBus link) rather than real bookable
        # items - pull them out before diffing/state storage so they never
        # get treated as availability data and never pollute state.json.
        meta_entry = fresh.pop("__meta__", None)
        city_ids = meta_entry.get("city_ids") if meta_entry else None

        full_list = get_full_availability_with_new_flags(wid, fresh, states)
        update_state_success(wid, fresh, states, now_iso)

        has_new = any(item.get("is_new") for item in full_list)
        if has_new:
            digest_sections.append({
                "watch_label": watch.label,
                "watch_url": _watch_url(watch, city_ids),
                "type": watch.type,
                "items": full_list,
            })
            summary["emailed_watches"].append(wid)

    if digest_sections:
        route_count = len(digest_sections)
        subject = (
            f"[RedBus Watcher] Seats available on {route_count} watch"
            f"{'es' if route_count != 1 else ''}!"
        )
        html = build_digest_email(digest_sections)
        send_email_fn(subject, html)

    return summary


def main():
    try:
        watches = load_watches_from_env()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    state_path = os.environ.get("REDBUS_STATE_PATH", "state.json")
    states = load_state(state_path)

    fail_threshold = int(os.environ.get("REDBUS_FAIL_THRESHOLD", str(FAIL_THRESHOLD_DEFAULT)))

    # Real wiring - imported lazily so unit tests never need playwright/resend installed
    # just to exercise run_once() with fakes.
    from redbus_watch.scraper_bus import fetch_bus_availability
    from redbus_watch.scraper_train import fetch_train_availability
    from redbus_watch.notifier import send_email

    def send_email_fn(subject: str, html: str):
        send_email(subject, html)

    summary = run_once(
        watches=watches,
        states=states,
        fetch_bus_fn=fetch_bus_availability,
        fetch_train_fn=fetch_train_availability,
        send_email_fn=send_email_fn,
        fail_threshold=fail_threshold,
    )

    save_state(states, state_path)

    print(f"Run summary: {summary}")
    if summary["failed_watches"] and not summary["emailed_watches"]:
        # Non-zero exit if literally everything failed, so the Actions run is visibly red
        if len(summary["failed_watches"]) == len(watches):
            sys.exit(1)


if __name__ == "__main__":
    main()
