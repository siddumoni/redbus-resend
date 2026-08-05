"""
State persistence and diffing.

Same pattern as bms-resend: a single JSON file committed back to the repo
by the GitHub Actions workflow, keyed by watch_id. We store the last-known
availability snapshot for each watch, and compare it against the fresh
snapshot fetched this run to decide whether an email is warranted.

IMPORTANT lesson carried over from the BMS false-alert bug: when a fetch
fails (site error, block, timeout) we must NOT overwrite the previous
state with an empty/failed result, or the next successful fetch will look
like a brand-new availability event and fire a false alert. Callers should
only call `update_state` with results from a *successful* fetch, and use
`record_fetch_failure` / `record_fetch_success` separately for the
consecutive-failure alerting.
"""

import json
import os
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional


DEFAULT_STATE_PATH = "state.json"


@dataclass
class SeatSnapshot:
    """One bookable option for a watch: a bus's seat count, or a train class."""
    key: str  # e.g. "Seater" bus operator name, or "SL" train class
    label: str  # human readable, e.g. "SRS Travels - Non AC Sleeper"
    available: int  # seat count, or 0/1 for simple avail flags
    price: Optional[float] = None
    status_text: Optional[str] = None  # e.g. "RAC 4", "WL 12", raw text if not numeric


@dataclass
class WatchState:
    watch_id: str
    last_snapshots: Dict[str, dict] = field(default_factory=dict)  # key -> SeatSnapshot dict
    consecutive_failures: int = 0
    last_success_ts: Optional[str] = None
    last_alert_ts: Optional[str] = None


def load_state(path: str = DEFAULT_STATE_PATH) -> Dict[str, WatchState]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    result = {}
    for watch_id, entry in raw.items():
        result[watch_id] = WatchState(
            watch_id=watch_id,
            last_snapshots=entry.get("last_snapshots", {}),
            consecutive_failures=entry.get("consecutive_failures", 0),
            last_success_ts=entry.get("last_success_ts"),
            last_alert_ts=entry.get("last_alert_ts"),
        )
    return result


def save_state(states: Dict[str, WatchState], path: str = DEFAULT_STATE_PATH) -> None:
    serializable = {wid: asdict(ws) for wid, ws in states.items()}
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False, sort_keys=True)
    os.replace(tmp_path, path)


def _is_available(snapshot: dict) -> bool:
    """
    True if a snapshot represents a bookable option.
    Handles both numeric seat counts and train status text (RAC/WL/AVAILABLE).
    """
    available = snapshot.get("available", 0)
    if isinstance(available, (int, float)) and available > 0:
        return True
    status = (snapshot.get("status_text") or "").strip().upper()
    if status.startswith("AVAILABLE") or status.startswith("RAC"):
        return True
    # WL (waiting list) and NOT AVAILABLE / SOLD OUT are not considered available
    return False


def diff_new_availability(
    watch_id: str,
    fresh_snapshots: Dict[str, dict],
    states: Dict[str, WatchState],
) -> List[dict]:
    """
    Compare fresh snapshots against last-known state for this watch.
    Returns the list of snapshots that are newly available (were not
    available last time, or are appearing for the first time).

    A snapshot is "newly available" if:
      - it wasn't in the previous state at all (new bus/train appeared) AND is available, OR
      - it was in the previous state but was NOT available, and now IS available.

    We deliberately do NOT alert when something *stays* available across
    runs - only on the transition, so you don't get spammed every 10
    minutes for the same already-known seat.
    """
    prev = states.get(watch_id)
    prev_snapshots = prev.last_snapshots if prev else {}

    newly_available = []
    for key, fresh in fresh_snapshots.items():
        was_available = _is_available(prev_snapshots.get(key, {"available": 0}))
        is_available_now = _is_available(fresh)
        if is_available_now and not was_available:
            newly_available.append(fresh)

    return newly_available


def get_full_availability_with_new_flags(
    watch_id: str,
    fresh_snapshots: Dict[str, dict],
    states: Dict[str, WatchState],
) -> List[dict]:
    """
    Returns EVERY currently-available snapshot (the full current picture,
    not just the delta), each with an added "is_new" key:
      - True  if it was NOT available last run (or didn't exist before)
      - False if it was already available last run too

    Order is preserved from fresh_snapshots (which scraper_bus.py already
    sorts by price ascending), so the full list stays price-sorted.

    Use this (instead of / alongside diff_new_availability) when you want
    the email to show "here's everything available right now" rather than
    only what's new - while still being able to highlight the new ones.
    """
    prev = states.get(watch_id)
    prev_snapshots = prev.last_snapshots if prev else {}

    result = []
    for key, fresh in fresh_snapshots.items():
        if not _is_available(fresh):
            continue
        was_available = _is_available(prev_snapshots.get(key, {"available": 0}))
        annotated = dict(fresh)
        annotated["is_new"] = not was_available
        result.append(annotated)

    return result


def update_state_success(
    watch_id: str,
    fresh_snapshots: Dict[str, dict],
    states: Dict[str, WatchState],
    now_iso: str,
) -> None:
    """Call this ONLY after a successful fetch. Resets failure streak."""
    states[watch_id] = WatchState(
        watch_id=watch_id,
        last_snapshots=fresh_snapshots,
        consecutive_failures=0,
        last_success_ts=now_iso,
        last_alert_ts=states.get(watch_id).last_alert_ts if watch_id in states else None,
    )


def record_fetch_failure(watch_id: str, states: Dict[str, WatchState]) -> int:
    """
    Call this when a fetch fails for a watch. Does NOT touch last_snapshots,
    so a subsequent successful fetch is compared against the true last-known
    good state, not a wiped one. Returns the new consecutive_failures count.
    """
    existing = states.get(watch_id)
    if existing is None:
        existing = WatchState(watch_id=watch_id)
    existing.consecutive_failures += 1
    states[watch_id] = existing
    return existing.consecutive_failures


def mark_alerted(watch_id: str, states: Dict[str, WatchState], now_iso: str) -> None:
    if watch_id in states:
        states[watch_id].last_alert_ts = now_iso
