from redbus_watch.email_template import (
    build_digest_email, build_failure_alert_email, _chip_level_for_count, _chip_level_for_status
)


def test_digest_email_includes_bus_section_and_operator():
    sections = [{
        "watch_label": "Chennai \u2192 Bangalore (2026-08-15)",
        "watch_url": "https://www.redbus.in/search?fromCity=Chennai",
        "type": "bus",
        "items": [{"operator": "SRS Travels", "bus_type": "AC Sleeper", "available": 6,
                   "price": 899.0, "departure_time": "22:30"}],
    }]
    html = build_digest_email(sections)
    assert "SRS Travels" in html
    assert "AC Sleeper" in html
    assert "6 seats left" in html
    assert "899" in html
    assert "Chennai" in html
    assert "<!DOCTYPE html>" in html


def test_digest_email_includes_train_section_and_status():
    sections = [{
        "watch_label": "MAS \u2192 SBC (2026-08-20)",
        "watch_url": "https://www.redbus.in/trains/search?fromStation=MAS",
        "type": "train",
        "items": [{"train_name": "Shatabdi Express", "train_no": "12007", "class": "CC",
                   "status_text": "AVAILABLE 24", "departure_time": "06:00"}],
    }]
    html = build_digest_email(sections)
    assert "Shatabdi Express" in html
    assert "12007" in html
    assert "AVAILABLE 24" in html


def test_digest_email_escapes_html_in_user_controlled_fields():
    sections = [{
        "watch_label": "<script>alert(1)</script>",
        "watch_url": "https://example.com",
        "type": "bus",
        "items": [{"operator": "<b>Evil</b>", "bus_type": "x", "available": 1}],
    }]
    html = build_digest_email(sections)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>Evil</b>" not in html


def test_multiple_sections_all_present():
    sections = [
        {"watch_label": "Route A", "watch_url": "https://a.com", "type": "bus",
         "items": [{"operator": "Op1", "bus_type": "Seater", "available": 2}]},
        {"watch_label": "Route B", "watch_url": "https://b.com", "type": "train",
         "items": [{"train_name": "Train1", "train_no": "111", "class": "SL", "status_text": "AVAILABLE"}]},
    ]
    html = build_digest_email(sections)
    assert "Route A" in html
    assert "Route B" in html
    assert "Op1" in html
    assert "Train1" in html


def test_chip_level_thresholds():
    assert _chip_level_for_count(10) == "high"
    assert _chip_level_for_count(5) == "high"
    assert _chip_level_for_count(4) == "limited"
    assert _chip_level_for_count(1) == "limited"
    assert _chip_level_for_count(0) == "none"


def test_chip_level_for_status_text():
    assert _chip_level_for_status("AVAILABLE 12") == "high"
    assert _chip_level_for_status("RAC 4") == "limited"
    assert _chip_level_for_status("WL 20") == "none"
    assert _chip_level_for_status("NOT AVAILABLE") == "none"


def test_failure_alert_email_contains_watch_label_and_counts():
    html = build_failure_alert_email("Chennai \u2192 Bangalore", 3, 3)
    assert "Chennai" in html
    assert "3" in html
    assert "<!DOCTYPE html>" in html


def test_new_badge_shown_for_items_flagged_is_new():
    sections = [{
        "watch_label": "Chennai \u2192 Bangalore (2026-08-15)",
        "watch_url": "https://example.com",
        "type": "bus",
        "items": [{"operator": "SRS Travels", "bus_type": "Sleeper", "available": 4, "is_new": True}],
    }]
    html = build_digest_email(sections)
    assert "NEW" in html


def test_new_badge_not_shown_for_items_not_flagged():
    sections = [{
        "watch_label": "Chennai \u2192 Bangalore (2026-08-15)",
        "watch_url": "https://example.com",
        "type": "bus",
        "items": [{"operator": "SRS Travels", "bus_type": "Sleeper", "available": 4, "is_new": False}],
    }]
    html = build_digest_email(sections)
    assert "NEW</span>" not in html


def test_full_list_with_mixed_new_and_old_items_shows_both():
    sections = [{
        "watch_label": "Chennai \u2192 Bangalore (2026-08-15)",
        "watch_url": "https://example.com",
        "type": "bus",
        "items": [
            {"operator": "SRS Travels", "bus_type": "Sleeper", "available": 4, "is_new": False},
            {"operator": "KPN Travels", "bus_type": "Seater", "available": 2, "is_new": True},
        ],
    }]
    html = build_digest_email(sections)
    assert "SRS Travels" in html
    assert "KPN Travels" in html
    assert html.count("NEW</span>") == 1  # only KPN gets the badge


def test_bus_item_shows_rating_badge_when_present():
    sections = [{
        "watch_label": "Route", "watch_url": "https://example.com", "type": "bus",
        "items": [{"operator": "Jai Sai Baba Travels", "bus_type": "Sleeper", "available": 24,
                   "rating": 4.9, "reviews": 1232, "price": 1190, "departure_time": "23:00",
                   "arrival_time": "05:50", "duration_min": 410, "total_seats": 37, "single_seats": 7}],
    }]
    html = build_digest_email(sections)
    assert "4.9" in html
    assert "1232" in html


def test_bus_item_no_rating_badge_when_rating_missing():
    sections = [{
        "watch_label": "Route", "watch_url": "https://example.com", "type": "bus",
        "items": [{"operator": "Some Operator", "bus_type": "Sleeper", "available": 4}],
    }]
    html = build_digest_email(sections)
    assert "&#9733;" not in html  # no star badge rendered when rating is absent


def test_bus_item_shows_duration_and_seats_with_single_count():
    sections = [{
        "watch_label": "Route", "watch_url": "https://example.com", "type": "bus",
        "items": [{"operator": "Op", "bus_type": "Sleeper", "available": 24, "total_seats": 37,
                   "single_seats": 7, "duration_min": 410, "price": 1190,
                   "departure_time": "23:00", "arrival_time": "05:50"}],
    }]
    html = build_digest_email(sections)
    assert "6h 50m" in html
    assert "37 Seats" in html
    assert "7 Single" in html


def test_bus_item_price_shown_with_onwards_label():
    sections = [{
        "watch_label": "Route", "watch_url": "https://example.com", "type": "bus",
        "items": [{"operator": "Op", "bus_type": "Sleeper", "available": 4, "price": 2500,
                   "departure_time": "21:25", "arrival_time": "07:50"}],
    }]
    html = build_digest_email(sections)
    assert "\u20b92,500" in html
    assert "Onwards" in html


def test_bus_item_no_day_offset_suffix_in_arrival():
    sections = [{
        "watch_label": "Route", "watch_url": "https://example.com", "type": "bus",
        "items": [{"operator": "Op", "bus_type": "Sleeper", "available": 4,
                   "departure_time": "23:00", "arrival_time": "05:50"}],
    }]
    html = build_digest_email(sections)
    assert "+1d" not in html
    assert "05:50" in html
