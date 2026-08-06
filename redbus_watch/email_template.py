"""
Builds the HTML email body, styled consistently with the bms-resend
template: gradient header banner, chip-style availability badges,
chronological grouping, clickable links, IST timestamps.

This module is pure string-building with no external dependencies, so it's
fully unit-testable without touching RedBus or Resend at all.
"""

from datetime import datetime, timezone, timedelta
from html import escape
from typing import List, Dict

IST = timezone(timedelta(hours=5, minutes=30))

# Chip colors: green = plenty available, amber = limited (e.g. RAC or <5 seats), grey = fallback
CHIP_GREEN = "#1b8a4c"
CHIP_GREEN_BG = "#e6f7ed"
CHIP_AMBER = "#b3760a"
CHIP_AMBER_BG = "#fdf1de"
CHIP_GREY = "#5a6472"
CHIP_GREY_BG = "#eef0f2"

# NEW badge - distinct from availability chips, marks items that flipped
# to available since the last check (vs. items that were already known).
NEW_BADGE_COLOR = "#b3261e"
NEW_BADGE_BG = "#fee2e2"


def _now_ist_str() -> str:
    return datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")


def _new_badge() -> str:
    return (
        f'<span style="display:inline-block;padding:3px 8px;margin:0 6px 0 0;'
        f"border-radius:4px;font-size:11px;font-weight:700;letter-spacing:0.03em;"
        f'color:{NEW_BADGE_COLOR};background:{NEW_BADGE_BG};">'
        f"NEW</span>"
    )


def _rating_badge(rating, reviews) -> str:
    """RedBus-style rating badge: colored box with score + star, review
    count alongside. Returns empty string if rating data isn't available
    (older/partial data), rather than showing a broken "None" badge."""
    if rating is None:
        return ""
    try:
        rating_val = float(rating)
    except (TypeError, ValueError):
        return ""

    if rating_val >= 4.0:
        color = CHIP_GREEN
    elif rating_val >= 3.0:
        color = CHIP_AMBER
    else:
        color = CHIP_GREY

    reviews_html = f'<span style="font-size:11px;color:#8a94a3;margin-left:4px;">({reviews})</span>' if reviews else ""
    return (
        f'<span style="display:inline-block;padding:2px 7px;border-radius:4px;'
        f'font-size:12px;font-weight:700;color:#fff;background:{color};">'
        f"{rating_val:.1f} &#9733;</span>{reviews_html}"
    )


def _format_duration(minutes) -> str:
    if minutes is None:
        return ""
    try:
        total_min = int(minutes)
    except (TypeError, ValueError):
        return ""
    h, m = divmod(total_min, 60)
    return f"{h}h {m}m"


def _chip(text: str, level: str) -> str:
    if level == "high":
        color, bg = CHIP_GREEN, CHIP_GREEN_BG
    elif level == "limited":
        color, bg = CHIP_AMBER, CHIP_AMBER_BG
    else:
        color, bg = CHIP_GREY, CHIP_GREY_BG
    return (
        f'<span style="display:inline-block;padding:4px 10px;margin:3px 6px 3px 0;'
        f"border-radius:999px;font-size:12px;font-weight:600;"
        f'color:{color};background:{bg};border:1px solid {color}22;">'
        f"{escape(text)}</span>"
    )


def _chip_level_for_count(available: int) -> str:
    if available >= 5:
        return "high"
    if available > 0:
        return "limited"
    return "none"


def _chip_level_for_status(status_text: str) -> str:
    s = status_text.strip().upper()
    if s.startswith("AVAILABLE"):
        return "high"
    if s.startswith("RAC"):
        return "limited"
    return "none"


def _bus_item_html(item: dict, booking_url: str) -> str:
    operator = escape(item.get("operator", "Unknown operator"))
    bus_type = escape(item.get("bus_type", ""))
    available = item.get("available", 0)
    total_seats = item.get("total_seats")
    single_seats = item.get("single_seats")
    price = item.get("price")
    departure = escape(item.get("departure_time", ""))
    arrival = escape(item.get("arrival_time", ""))
    new_badge = _new_badge() if item.get("is_new") else ""
    rating_html = _rating_badge(item.get("rating"), item.get("reviews"))
    duration_str = _format_duration(item.get("duration_min"))

    seats_label = f"{total_seats} Seats" if total_seats else f"{available} seats left"
    if single_seats:
        seats_label += f" ({single_seats} Single)"
    meta_line = duration_str
    if meta_line:
        meta_line += f" &middot; {seats_label}"
    else:
        meta_line = seats_label

    timing_html = f"{departure}"
    if arrival:
        timing_html += f' <span style="color:#c2c7cf;font-weight:400;">&mdash;</span> {arrival}'

    price_html = f"\u20b9{price:,.0f}" if price else ""

    return f"""
    <tr>
      <td style="padding:16px 18px;border-bottom:1px solid #eceef1;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="font-size:18px;font-weight:700;color:#1a1d23;">{new_badge}{operator}</td>
            <td style="text-align:right;white-space:nowrap;">{rating_html}</td>
          </tr>
          <tr>
            <td colspan="2" style="font-size:13px;color:#5a6472;padding-top:1px;">{bus_type}</td>
          </tr>
          <tr>
            <td style="font-size:15px;font-weight:600;color:#1a1d23;padding-top:10px;">{timing_html}</td>
            <td style="font-size:15px;font-weight:600;color:#1a1d23;text-align:right;padding-top:10px;white-space:nowrap;">{price_html}</td>
          </tr>
          <tr>
            <td style="font-size:13px;color:#8a94a3;padding-top:2px;">{meta_line}</td>
            <td style="font-size:13px;color:#8a94a3;padding-top:2px;text-align:right;">{"Onwards" if price else ""}</td>
          </tr>
        </table>
      </td>
    </tr>"""


def _train_item_html(item: dict, booking_url: str) -> str:
    train_name = escape(item.get("train_name", ""))
    train_no = escape(item.get("train_no", ""))
    cls = escape(item.get("class", ""))
    status_text = item.get("status_text", "")
    departure = escape(item.get("departure_time", ""))
    new_badge = _new_badge() if item.get("is_new") else ""

    chip = _chip(status_text or "Available", _chip_level_for_status(status_text or ""))

    return f"""
    <tr>
      <td style="padding:14px 18px;border-bottom:1px solid #eceef1;">
        <div style="font-size:15px;font-weight:600;color:#1a1d23;">{new_badge}{train_name} ({train_no})</div>
        <div style="font-size:13px;color:#5a6472;margin-top:2px;">Class {cls} &middot; Departs {departure}</div>
        <div style="margin-top:8px;">{chip}</div>
      </td>
    </tr>"""


def _watch_section_html(watch_label: str, watch_url: str, items_html: str) -> str:
    return f"""
  <tr>
    <td style="padding:22px 0 6px 0;">
      <a href="{escape(watch_url)}" style="font-size:16px;font-weight:700;color:#0f62d6;text-decoration:none;">
        {escape(watch_label)} &rarr;
      </a>
    </td>
  </tr>
  <tr>
    <td>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border:1px solid #eceef1;border-radius:10px;overflow:hidden;">
        {items_html}
      </table>
    </td>
  </tr>"""


def build_digest_email(sections: List[Dict]) -> str:
    """
    sections: list of dicts, each:
      {
        "watch_label": str,
        "watch_url": str,
        "type": "bus" | "train",
        "items": [ {...bus or train fields...} ]
      }
    Returns full HTML document string.
    """
    body_sections = []
    for section in sections:
        if section["type"] == "bus":
            items_html = "".join(_bus_item_html(i, section["watch_url"]) for i in section["items"])
        else:
            items_html = "".join(_train_item_html(i, section["watch_url"]) for i in section["items"])
        body_sections.append(
            _watch_section_html(section["watch_label"], section["watch_url"], items_html)
        )

    sections_html = "".join(body_sections)
    timestamp = _now_ist_str()

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.06);">
          <tr>
            <td style="background:linear-gradient(135deg,#0f62d6 0%,#7c3aed 100%);padding:28px 24px;">
              <div style="font-size:20px;font-weight:700;color:#ffffff;">Seats just opened up \U0001F68C\U0001F686</div>
              <div style="font-size:13px;color:#e4e9ff;margin-top:4px;">RedBus availability alert &middot; {timestamp}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:6px 24px 24px 24px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {sections_html}
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 24px;background:#f9fafb;border-top:1px solid #eceef1;">
              <div style="font-size:12px;color:#8a94a3;">
                You're receiving this because this route/date is in your RedBus watch list. Availability can change fast &mdash; book quickly once you get this.
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def build_failure_alert_email(watch_label: str, consecutive_failures: int, threshold: int) -> str:
    """Mirrors the BMS consecutive-failure alert - lets you know the watcher itself is broken."""
    timestamp = _now_ist_str()
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="560" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.06);">
          <tr>
            <td style="background:#b3261e;padding:22px 24px;">
              <div style="font-size:18px;font-weight:700;color:#ffffff;">Watcher fetch failing repeatedly</div>
              <div style="font-size:13px;color:#ffd9d6;margin-top:4px;">{timestamp}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 24px;">
              <p style="font-size:14px;color:#1a1d23;line-height:1.5;">
                The watch <strong>{escape(watch_label)}</strong> has failed to fetch
                <strong>{consecutive_failures}</strong> times in a row (threshold: {threshold}).
                RedBus may be blocking requests, or the page structure may have changed.
              </p>
              <p style="font-size:13px;color:#5a6472;">Check the GitHub Actions run logs for details.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
