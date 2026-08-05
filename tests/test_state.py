import os
import tempfile
from redbus_watch.state import (
    load_state, save_state, diff_new_availability,
    update_state_success, record_fetch_failure, WatchState,
    get_full_availability_with_new_flags,
)


def test_diff_flags_brand_new_available_item():
    states = {}
    fresh = {"bus1": {"available": 3}}
    newly = diff_new_availability("watch1", fresh, states)
    assert newly == [{"available": 3}]


def test_diff_flags_transition_from_zero_to_available():
    states = {"watch1": WatchState(watch_id="watch1", last_snapshots={"bus1": {"available": 0}})}
    fresh = {"bus1": {"available": 2}}
    newly = diff_new_availability("watch1", fresh, states)
    assert len(newly) == 1
    assert newly[0]["available"] == 2


def test_diff_does_not_refire_when_still_available():
    states = {"watch1": WatchState(watch_id="watch1", last_snapshots={"bus1": {"available": 5}})}
    fresh = {"bus1": {"available": 3}}  # still available, just fewer seats
    newly = diff_new_availability("watch1", fresh, states)
    assert newly == []


def test_diff_does_not_fire_when_still_unavailable():
    states = {"watch1": WatchState(watch_id="watch1", last_snapshots={"bus1": {"available": 0}})}
    fresh = {"bus1": {"available": 0}}
    newly = diff_new_availability("watch1", fresh, states)
    assert newly == []


def test_diff_handles_train_status_text_rac_as_available():
    states = {"watch1": WatchState(watch_id="watch1", last_snapshots={"cls1": {"status_text": "WL 12"}})}
    fresh = {"cls1": {"status_text": "RAC 3"}}
    newly = diff_new_availability("watch1", fresh, states)
    assert len(newly) == 1


def test_diff_does_not_treat_waitlist_as_available():
    states = {}
    fresh = {"cls1": {"status_text": "WL 12"}}
    newly = diff_new_availability("watch1", fresh, states)
    assert newly == []


def test_failed_fetch_does_not_wipe_previous_state():
    """
    Regression test for the exact bug class you hit with BMS: a failed
    fetch must never overwrite last_snapshots, or the next successful
    fetch looks like a brand new availability event (false alert).
    """
    states = {"watch1": WatchState(watch_id="watch1", last_snapshots={"bus1": {"available": 4}})}
    record_fetch_failure("watch1", states)
    # last_snapshots must be untouched
    assert states["watch1"].last_snapshots == {"bus1": {"available": 4}}
    assert states["watch1"].consecutive_failures == 1

    # Next successful fetch with the SAME data should NOT look "newly available"
    fresh = {"bus1": {"available": 4}}
    newly = diff_new_availability("watch1", fresh, states)
    assert newly == []


def test_consecutive_failures_increment_and_reset_on_success():
    states = {}
    record_fetch_failure("watch1", states)
    record_fetch_failure("watch1", states)
    assert states["watch1"].consecutive_failures == 2

    update_state_success("watch1", {"bus1": {"available": 1}}, states, now_iso="2026-08-03T00:00:00Z")
    assert states["watch1"].consecutive_failures == 0


def test_save_and_load_state_roundtrip():
    states = {
        "watch1": WatchState(
            watch_id="watch1",
            last_snapshots={"bus1": {"available": 2, "operator": "SRS Travels"}},
            consecutive_failures=1,
            last_success_ts="2026-08-01T00:00:00Z",
            last_alert_ts=None,
        )
    }
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "state.json")
        save_state(states, path)
        loaded = load_state(path)

    assert loaded["watch1"].last_snapshots == {"bus1": {"available": 2, "operator": "SRS Travels"}}
    assert loaded["watch1"].consecutive_failures == 1
    assert loaded["watch1"].last_success_ts == "2026-08-01T00:00:00Z"


def test_load_state_missing_file_returns_empty():
    assert load_state("/tmp/does_not_exist_redbus_state.json") == {}


def test_full_availability_marks_brand_new_item_as_new():
    states = {}
    fresh = {"bus1": {"available": 3}}
    result = get_full_availability_with_new_flags("watch1", fresh, states)
    assert len(result) == 1
    assert result[0]["is_new"] is True


def test_full_availability_includes_both_old_and_new_items():
    """
    The core of the "full list + highlight new" feature: when bus A was
    already available last run and bus B just became available, the full
    list must include BOTH - A marked not-new, B marked new - not just B.
    """
    states = {
        "watch1": WatchState(watch_id="watch1", last_snapshots={
            "busA": {"available": 5},
            "busB": {"available": 0},
        })
    }
    fresh = {
        "busA": {"available": 4},   # still available, just fewer seats
        "busB": {"available": 2},   # just became available
    }
    result = get_full_availability_with_new_flags("watch1", fresh, states)
    assert len(result) == 2

    is_new_by_available_count = {r["available"]: r["is_new"] for r in result}
    assert is_new_by_available_count[4] is False  # busA - was already available
    assert is_new_by_available_count[2] is True   # busB - just became available


def test_full_availability_excludes_unavailable_items():
    states = {}
    fresh = {"bus1": {"available": 0}, "bus2": {"available": 3}}
    result = get_full_availability_with_new_flags("watch1", fresh, states)
    assert len(result) == 1
    assert result[0]["available"] == 3


def test_full_availability_preserves_input_order():
    """Order must be preserved since scraper_bus.py sorts by price - the
    full list should stay price-sorted, not get reshuffled."""
    states = {}
    fresh = {
        "cheap": {"available": 1, "price": 500},
        "mid": {"available": 1, "price": 900},
        "expensive": {"available": 1, "price": 1500},
    }
    result = get_full_availability_with_new_flags("watch1", fresh, states)
    assert [r["price"] for r in result] == [500, 900, 1500]


def test_full_availability_no_new_items_all_flagged_false():
    states = {"watch1": WatchState(watch_id="watch1", last_snapshots={"bus1": {"available": 5}})}
    fresh = {"bus1": {"available": 3}}
    result = get_full_availability_with_new_flags("watch1", fresh, states)
    assert result[0]["is_new"] is False
