"""
Config loader for the RedBus watcher.

Reads the REDBUS_WATCHES environment variable, a JSON array where each
entry describes one thing to monitor. Each entry can expand into multiple
Watch objects (one per date) if "dates" is used instead of "date".

Example REDBUS_WATCHES value:

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
    "classes": ["SL", "3A"]
  }
]

Notes on fields:
- type: "bus" or "train" (required)
- source / destination: city names for bus, station codes for train (required)
- date: single date, YYYY-MM-DD - use this OR "dates", not both
- dates: list of YYYY-MM-DD dates - watches every date as a separate watch
  under one entry, so you don't have to duplicate the whole block per date
- label: human-readable name used in the email; auto-generated if omitted.
  When "dates" produces multiple watches, the date is appended to keep
  each one distinguishable.
- seat_type (bus only): "any", "seater", "sleeper", "ac", "non_ac" - filters
  which seat types count as a trigger. Defaults to "any".
- operators (bus only): list of operator name substrings to watch for
  (case-insensitive, e.g. ["SRS", "KPN Travels"]). If omitted, any operator
  counts. Use this to watch for one particular bus/operator instead of
  "any bus on this route".
- classes (train only): list of class codes to watch (e.g. ["SL", "3A", "2A"]).
  Defaults to all classes returned by the search.
"""

import json
import os
import sys
from dataclasses import dataclass
from typing import List, Optional


VALID_TYPES = {"bus", "train"}
VALID_SEAT_TYPES = {"any", "seater", "sleeper", "ac", "non_ac"}


class ConfigError(ValueError):
    """Raised when REDBUS_WATCHES is missing or malformed."""


@dataclass
class Watch:
    type: str
    source: str
    destination: str
    date: str
    label: str
    seat_type: str = "any"
    classes: Optional[List[str]] = None
    operators: Optional[List[str]] = None

    def watch_id(self) -> str:
        """Stable identifier used as the state-file key for this watch."""
        base = f"{self.type}:{self.source}:{self.destination}:{self.date}"
        if self.type == "train" and self.classes:
            base += ":" + ",".join(sorted(self.classes))
        if self.type == "bus" and self.operators:
            base += ":" + ",".join(sorted(o.lower() for o in self.operators))
        return base.lower().replace(" ", "_")


def _extract_dates(raw: dict, idx: int) -> List[str]:
    has_date = "date" in raw and raw.get("date")
    has_dates = "dates" in raw

    if has_date and has_dates:
        raise ConfigError(
            f"REDBUS_WATCHES entry #{idx}: specify either 'date' or 'dates', not both"
        )
    if not has_date and not has_dates:
        raise ConfigError(
            f"REDBUS_WATCHES entry #{idx} is missing required field: 'date' (or 'dates')"
        )

    if has_dates:
        dates_raw = raw["dates"]
        if not isinstance(dates_raw, list) or not all(isinstance(d, str) for d in dates_raw):
            raise ConfigError(f"REDBUS_WATCHES entry #{idx}: 'dates' must be a list of strings")
        if len(dates_raw) == 0:
            raise ConfigError(f"REDBUS_WATCHES entry #{idx}: 'dates' is an empty list")
        return [d.strip() for d in dates_raw]

    return [str(raw["date"]).strip()]


def _extract_operators(raw: dict, idx: int) -> Optional[List[str]]:
    operators = raw.get("operators")
    if operators is None:
        return None
    if not isinstance(operators, list) or not all(isinstance(o, str) for o in operators):
        raise ConfigError(f"REDBUS_WATCHES entry #{idx}: 'operators' must be a list of strings")
    cleaned = [o.strip() for o in operators if o.strip()]
    return cleaned or None  # empty/whitespace-only list == no filter


def _validate_entry(raw: dict, idx: int) -> List[Watch]:
    missing = [k for k in ("type", "source", "destination") if not raw.get(k)]
    if missing:
        raise ConfigError(
            f"REDBUS_WATCHES entry #{idx} is missing required field(s): {', '.join(missing)}"
        )

    watch_type = str(raw["type"]).strip().lower()
    if watch_type not in VALID_TYPES:
        raise ConfigError(
            f"REDBUS_WATCHES entry #{idx} has invalid type '{raw['type']}'. "
            f"Must be one of {sorted(VALID_TYPES)}"
        )

    seat_type = str(raw.get("seat_type", "any")).strip().lower()
    if seat_type not in VALID_SEAT_TYPES:
        raise ConfigError(
            f"REDBUS_WATCHES entry #{idx} has invalid seat_type '{raw.get('seat_type')}'. "
            f"Must be one of {sorted(VALID_SEAT_TYPES)}"
        )

    classes = raw.get("classes")
    if classes is not None:
        if not isinstance(classes, list) or not all(isinstance(c, str) for c in classes):
            raise ConfigError(f"REDBUS_WATCHES entry #{idx}: 'classes' must be a list of strings")
        classes = [c.strip().upper() for c in classes]

    operators = _extract_operators(raw, idx)
    date_values = _extract_dates(raw, idx)

    source = str(raw["source"]).strip()
    destination = str(raw["destination"]).strip()
    base_label = raw.get("label")

    watches = []
    for date in date_values:
        if base_label:
            label = str(base_label) if len(date_values) == 1 else f"{base_label} ({date})"
        else:
            label = f"{source} \u2192 {destination} ({date})"

        watches.append(Watch(
            type=watch_type,
            source=source,
            destination=destination,
            date=date,
            label=label,
            seat_type=seat_type,
            classes=classes,
            operators=operators,
        ))

    return watches


def parse_watches(raw_json: str) -> List[Watch]:
    """Parse and validate the REDBUS_WATCHES JSON string into Watch objects."""
    if raw_json is None or not raw_json.strip():
        raise ConfigError("REDBUS_WATCHES is empty or not set")

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ConfigError(f"REDBUS_WATCHES is not valid JSON: {e}") from e

    if not isinstance(data, list):
        raise ConfigError("REDBUS_WATCHES must be a JSON array")

    if len(data) == 0:
        raise ConfigError("REDBUS_WATCHES is an empty array - nothing to watch")

    watches: List[Watch] = []
    for i, entry in enumerate(data):
        watches.extend(_validate_entry(entry, i))

    # Duplicate watch_id detection - two identical watches would silently
    # clobber each other's state, so fail loudly instead.
    seen = {}
    for w in watches:
        wid = w.watch_id()
        if wid in seen:
            raise ConfigError(
                f"Duplicate watch detected: '{w.label}' and '{seen[wid].label}' "
                f"resolve to the same watch_id ({wid})"
            )
        seen[wid] = w

    return watches


def load_watches_from_env() -> List[Watch]:
    raw = os.environ.get("REDBUS_WATCHES", "")
    return parse_watches(raw)


if __name__ == "__main__":
    try:
        watches = load_watches_from_env()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)
    for w in watches:
        print(w)
