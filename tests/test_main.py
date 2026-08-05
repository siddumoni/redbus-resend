from redbus_watch.config import parse_watches
from redbus_watch.main import run_once
import json


def make_watches(raw_list):
    return parse_watches(json.dumps(raw_list))


def test_sends_email_when_bus_seat_becomes_available():
    watches = make_watches([
        {"type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15"}
    ])
    states = {}
    sent = []

    def fake_fetch_bus(source, destination, date, seat_type, operators):
        return {"bus1": {"operator": "SRS Travels", "bus_type": "Sleeper", "available": 4, "departure_time": "22:00"}}

    def fake_fetch_train(*a, **kw):
        raise AssertionError("should not be called for a bus watch")

    def fake_send(subject, html):
        sent.append((subject, html))

    summary = run_once(watches, states, fake_fetch_bus, fake_fetch_train, fake_send)

    assert len(sent) == 1
    assert "Seats available" in sent[0][0]
    assert "SRS Travels" in sent[0][1]
    assert summary["emailed_watches"] == [watches[0].watch_id()]


def test_no_email_when_nothing_newly_available():
    watches = make_watches([
        {"type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15"}
    ])
    states = {}
    sent = []

    def fake_fetch_bus(*a, **kw):
        return {"bus1": {"available": 0}}

    def fake_send(subject, html):
        sent.append((subject, html))

    run_once(watches, states, fake_fetch_bus, lambda *a, **kw: {}, fake_send)
    assert sent == []


def test_no_repeat_email_on_second_run_with_same_availability():
    """Confirms we don't spam every 10 minutes for the same still-available seat."""
    watches = make_watches([
        {"type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15"}
    ])
    states = {}
    sent = []

    def fake_fetch_bus(*a, **kw):
        return {"bus1": {"operator": "SRS", "bus_type": "Sleeper", "available": 4, "departure_time": "22:00"}}

    def fake_send(subject, html):
        sent.append((subject, html))

    run_once(watches, states, fake_fetch_bus, lambda *a, **kw: {}, fake_send)
    assert len(sent) == 1

    # second run, same data
    run_once(watches, states, fake_fetch_bus, lambda *a, **kw: {}, fake_send)
    assert len(sent) == 1  # still just one email total


def test_fetch_failure_does_not_crash_and_tracks_failure_count():
    watches = make_watches([
        {"type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15"}
    ])
    states = {}
    sent = []

    def failing_fetch_bus(*a, **kw):
        raise RuntimeError("site blocked us")

    def fake_send(subject, html):
        sent.append((subject, html))

    summary = run_once(watches, states, failing_fetch_bus, lambda *a, **kw: {}, fake_send, fail_threshold=3)
    assert summary["failed_watches"] == [watches[0].watch_id()]
    assert states[watches[0].watch_id()].consecutive_failures == 1
    assert sent == []  # below threshold, no failure alert yet


def test_failure_alert_fires_at_threshold():
    watches = make_watches([
        {"type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15"}
    ])
    states = {}
    sent = []

    def failing_fetch_bus(*a, **kw):
        raise RuntimeError("site blocked us")

    def fake_send(subject, html):
        sent.append((subject, html))

    for _ in range(3):
        run_once(watches, states, failing_fetch_bus, lambda *a, **kw: {}, fake_send, fail_threshold=3)

    assert len(sent) == 1
    assert "Repeated failures" in sent[0][0]


def test_train_watch_uses_train_fetcher_and_status_text():
    watches = make_watches([
        {"type": "train", "source": "MAS", "destination": "SBC", "date": "2026-08-20", "classes": ["SL"]}
    ])
    states = {}
    sent = []

    def fake_fetch_train(source, destination, date, classes):
        assert classes == ["SL"]
        return {"12007_SL": {"train_name": "Test Exp", "train_no": "12007", "class": "SL",
                              "status_text": "AVAILABLE 10", "departure_time": "06:00"}}

    def fake_send(subject, html):
        sent.append((subject, html))

    run_once(watches, states, lambda *a, **kw: {}, fake_fetch_train, fake_send)
    assert len(sent) == 1
    assert "Test Exp" in sent[0][1]


def test_multiple_watches_combine_into_one_digest_email():
    watches = make_watches([
        {"type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15"},
        {"type": "train", "source": "MAS", "destination": "SBC", "date": "2026-08-20"},
    ])
    states = {}
    sent = []

    def fake_fetch_bus(*a, **kw):
        return {"b1": {"operator": "Op", "bus_type": "Seater", "available": 3, "departure_time": "10:00"}}

    def fake_fetch_train(*a, **kw):
        return {"t1": {"train_name": "T", "train_no": "1", "class": "SL", "status_text": "AVAILABLE"}}

    def fake_send(subject, html):
        sent.append((subject, html))

    run_once(watches, states, fake_fetch_bus, fake_fetch_train, fake_send)
    assert len(sent) == 1  # ONE combined digest, not two separate emails
    assert "2 watches" in sent[0][0]


def test_bus_watch_operators_are_passed_to_fetcher():
    watches = make_watches([{
        "type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15",
        "operators": ["SRS Travels"],
    }])
    states = {}
    received_args = {}

    def fake_fetch_bus(source, destination, date, seat_type, operators):
        received_args["operators"] = operators
        return {}

    run_once(watches, states, fake_fetch_bus, lambda *a, **kw: {}, lambda *a, **kw: None)
    assert received_args["operators"] == ["SRS Travels"]


def test_meta_entry_is_used_for_link_and_not_treated_as_availability():
    """
    __meta__ carries the resolved city IDs for building a working RedBus
    link. It must never be treated as a bookable item (no false "seat
    available" alert) and must not end up persisted in state.json's
    per-bus snapshot data.
    """
    watches = make_watches([
        {"type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15"}
    ])
    states = {}
    sent = []

    def fake_fetch_bus(*a, **kw):
        return {
            "__meta__": {"city_ids": {"source": 123, "dest": 122}},
            "bus1": {"operator": "SRS", "bus_type": "Sleeper", "available": 4, "departure_time": "22:00"},
        }

    def fake_send(subject, html):
        sent.append((subject, html))

    run_once(watches, states, fake_fetch_bus, lambda *a, **kw: {}, fake_send)

    assert len(sent) == 1
    # The link should use the real resolved city IDs, not raw names
    assert "fromCity=123" in sent[0][1]
    assert "toCity=122" in sent[0][1]

    # __meta__ must not be persisted as a tracked "bus" in state
    wid = watches[0].watch_id()
    assert "__meta__" not in states[wid].last_snapshots


def test_meta_missing_falls_back_to_generic_link():
    watches = make_watches([
        {"type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15"}
    ])
    states = {}
    sent = []

    def fake_fetch_bus(*a, **kw):
        return {"bus1": {"operator": "SRS", "bus_type": "Sleeper", "available": 4, "departure_time": "22:00"}}

    def fake_send(subject, html):
        sent.append((subject, html))

    run_once(watches, states, fake_fetch_bus, lambda *a, **kw: {}, fake_send)
    assert len(sent) == 1
    assert "redbus.in" in sent[0][1]  # generic fallback link, no crash


def test_second_run_shows_full_list_with_only_new_bus_badged():
    """
    End-to-end version of the feature Siddu asked for: first run finds
    bus A (emailed, badged NEW). Second run, bus A is still available AND
    bus B just became available - the email should show BOTH, but only B
    gets the NEW badge.
    """
    watches = make_watches([
        {"type": "bus", "source": "Chennai", "destination": "Coimbatore", "date": "2026-08-15"}
    ])
    states = {}
    sent = []

    def fake_send(subject, html):
        sent.append((subject, html))

    def fetch_run_1(*a, **kw):
        return {"busA": {"operator": "SRS Travels", "bus_type": "Sleeper", "available": 5, "departure_time": "22:00"}}

    run_once(watches, states, fetch_run_1, lambda *a, **kw: {}, fake_send)
    assert len(sent) == 1
    assert "SRS Travels" in sent[0][1]

    def fetch_run_2(*a, **kw):
        return {
            "busA": {"operator": "SRS Travels", "bus_type": "Sleeper", "available": 3, "departure_time": "22:00"},
            "busB": {"operator": "KPN Travels", "bus_type": "Seater", "available": 2, "departure_time": "23:00"},
        }

    run_once(watches, states, fetch_run_2, lambda *a, **kw: {}, fake_send)
    assert len(sent) == 2  # second email sent because busB is new

    second_email_html = sent[1][1]
    # Both buses should appear in the full list...
    assert "SRS Travels" in second_email_html
    assert "KPN Travels" in second_email_html
    # ...but only busB (KPN) should carry the NEW badge
    assert second_email_html.count("NEW</span>") == 1


def test_no_new_email_when_available_bus_just_loses_seats_but_stays_available():
    watches = make_watches([
        {"type": "bus", "source": "Chennai", "destination": "Coimbatore", "date": "2026-08-15"}
    ])
    states = {}
    sent = []

    def fake_send(subject, html):
        sent.append((subject, html))

    def fetch_run_1(*a, **kw):
        return {"busA": {"operator": "SRS", "bus_type": "Sleeper", "available": 10, "departure_time": "22:00"}}

    run_once(watches, states, fetch_run_1, lambda *a, **kw: {}, fake_send)
    assert len(sent) == 1

    def fetch_run_2(*a, **kw):
        return {"busA": {"operator": "SRS", "bus_type": "Sleeper", "available": 2, "departure_time": "22:00"}}

    run_once(watches, states, fetch_run_2, lambda *a, **kw: {}, fake_send)
    assert len(sent) == 1  # still available, just fewer seats - no new email
