"""
Train availability adapter for RedBus.

Important:
- The actual RedBus train-search endpoint/schema has NOT been verified in this
  implementation. This module intentionally does not guess an endpoint.
- Run discover.py --mode train locally and inspect discovery_output before
  enabling scheduled train monitoring.
- Parsing is deliberately schema-tolerant, but it still requires explicit
  evidence that a captured response contains train inventory.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class ScrapeError(RuntimeError):
    """Raised when train availability could not be established reliably."""


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _first(obj: dict, *keys: str) -> Any:
    for key in keys:
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
    lowered = {str(k).lower(): v for k, v in obj.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _iter_dicts(value: Any) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _status_text(item: dict) -> str:
    raw = _first(
        item, "status_text", "statusText", "availabilityStatus",
        "availability_status", "availability", "currentStatus",
        "current_status", "bookingStatus", "booking_status", "status",
    )
    if isinstance(raw, dict):
        raw = _first(raw, "status", "text", "message", "availability")
    if isinstance(raw, list):
        raw = " / ".join(str(x) for x in raw)
    if raw is None:
        count = _first(item, "availableSeats", "available_seats", "seatsAvailable")
        if isinstance(count, (int, float)):
            return f"AVAILABLE {int(count)}" if count > 0 else "NOT AVAILABLE"
        return ""
    if isinstance(raw, (int, float)):
        return f"AVAILABLE {int(raw)}" if raw > 0 else "NOT AVAILABLE"
    return str(raw).strip()


def _is_inventory_item(item: dict) -> bool:
    train_no = _first(item, "train_no", "trainNo", "trainNumber", "train_number", "trainNoWithName")
    train_name = _first(item, "train_name", "trainName", "train_name_en", "name")
    # Do not accept arbitrary nested objects that happen to contain a status.
    return bool(train_no or train_name)


def _class_code(item: dict, parent_class: Optional[str] = None) -> str:
    value = _first(item, "class", "classCode", "class_code", "coachClass", "travelClass", "quotaClass")
    if value is None:
        value = parent_class
    if isinstance(value, dict):
        value = _first(value, "code", "name", "classCode")
    return str(value or "UNKNOWN").strip().upper()


def parse_train_search_response(
    payload: Any, classes_filter: Optional[List[str]] = None
) -> Dict[str, dict]:
    """
    Normalize a captured JSON response into:
      { "<train_number>_<class>": {
          "train_name", "train_no", "class", "available", "status_text",
          "departure_time", "arrival_time"
      }}

    Because RedBus's live train schema is not yet confirmed, this parser only
    accepts objects with a train number/name plus an explicit availability
    field. If it cannot identify train inventory, it raises ScrapeError rather
    than treating an unknown response as "no seats".
    """
    if not isinstance(payload, (dict, list)):
        raise ScrapeError("Train response is not a JSON object or array.")

    # Discovery files may wrap the body in {"result": {"parsed": ...}} or
    # {"body": ...}; unwrap only known capture wrappers.
    if isinstance(payload, dict):
        if isinstance(payload.get("result"), dict):
            result = payload["result"]
            if "parsed" in result and result["parsed"] is not None:
                payload = result["parsed"]
            elif "body" in result:
                payload = result["body"]
        elif "body" in payload and isinstance(payload["body"], (dict, list)):
            payload = payload["body"]

    candidates = []
    for item in _iter_dicts(payload):
        if not _is_inventory_item(item):
            continue
        status = _status_text(item)
        if not status:
            continue
        candidates.append((item, status))

    if not candidates:
        raise ScrapeError(
            "No recognizable train inventory with an explicit availability "
            "field was found. Capture a real train search with discover.py "
            "and update the field mapping; this is not treated as zero availability."
        )

    wanted = {str(c).strip().upper() for c in classes_filter or []}
    snapshots: Dict[str, dict] = {}
    for item, status in candidates:
        train_no = str(_first(
            item, "train_no", "trainNo", "trainNumber", "train_number", "number"
        ) or _first(item, "trainNoWithName") or "").strip()
        train_name = str(_first(
            item, "train_name", "trainName", "train_name_en", "name"
        ) or "").strip()
        class_code = _class_code(item)
        if wanted and class_code not in wanted:
            continue

        # A stable key is required for state diffing across workflow runs.
        identity = train_no or _norm(train_name)
        if not identity:
            continue
        key = f"{identity}_{class_code}".lower().replace(" ", "_")
        normalized_status = status.strip()
        upper = normalized_status.upper()
        available = 0
        match = re.search(r"\b(?:AVAILABLE|AVAILABILITY)\D{0,8}(\d+)\b", upper)
        if match:
            available = int(match.group(1))
        elif upper.startswith("AVAILABLE") or upper.startswith("RAC"):
            available = 1
        elif upper.isdigit():
            available = int(upper)

        snapshots[key] = {
            "train_name": train_name or f"Train {identity}",
            "train_no": train_no or identity,
            "class": class_code,
            "available": available,
            "status_text": normalized_status,
            "departure_time": str(_first(
                item, "departure_time", "departureTime", "departure", "departureTimeText"
            ) or ""),
            "arrival_time": str(_first(
                item, "arrival_time", "arrivalTime", "arrival", "arrivalTimeText"
            ) or ""),
        }

    if wanted and not snapshots:
        # The response was recognized, but no requested classes were found.
        # This is valid only if at least one inventory row was parsed.
        return {}
    return snapshots


def _looks_like_train_response(url: str, content_type: str) -> bool:
    """Conservative response filter; still requires the parser to validate data."""
    u = url.lower()
    ct = content_type.lower()
    return "json" in ct and any(
        word in u for word in ("train", "rail", "search", "avail", "schedule", "journey")
    )


def fetch_train_availability(
    source: str,
    destination: str,
    date: str,
    classes_filter: Optional[List[str]] = None,
) -> Dict[str, dict]:
    """
    Capture likely train JSON responses while a human navigates RedBus.

    This function intentionally fails closed until an exact train-search
    request is configured and validated. The interactive discovery workflow
    is the authoritative next step; do not rely on guessed station parameters.
    """
    if not os.environ.get("REDBUS_TRAIN_SEARCH_URL"):
        raise ScrapeError(
            "Train endpoint is not configured. Run `python discover.py --mode "
            "train --source <station> --destination <station> --date YYYY-MM-DD`, "
            "inspect discovery_output, then configure REDBUS_TRAIN_SEARCH_URL "
            "and implement the confirmed request in fetch_train_availability()."
        )

    # A URL alone is insufficient: request method, station identifiers, date
    # format, cookies and payload may be required. Do not make a speculative
    # request that could produce misleading availability.
    raise ScrapeError(
        "REDBUS_TRAIN_SEARCH_URL is set, but the verified train request adapter "
        "has not been implemented. Update this function using the real captured "
        "request method, parameters and response schema before enabling polling."
    )
