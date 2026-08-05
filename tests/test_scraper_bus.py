"""
These tests run the actual parsing logic against real data captured from
redbus.in (see tests/fixtures/real_*.json). This is the strongest kind of
test available here: it proves the field-name mapping is correct against
production data, not against my assumptions about what the data would
look like.
"""

import json
import os
import pytest

from redbus_watch.scraper_bus import (
    parse_bus_search_response,
    _extract_city_id,
    _date_to_redbus_format,
    ScrapeError,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def test_date_format_conversion():
    assert _date_to_redbus_format("2026-08-15") == "15-Aug-2026"
    assert _date_to_redbus_format("2026-01-05") == "05-Jan-2026"


def test_extract_city_id_from_real_source_response():
    fixture = load_fixture("real_source_city_suggestion.json")
    city_id = _extract_city_id(fixture["result"], "Chennai")
    assert city_id == 123


def test_extract_city_id_from_real_dest_response():
    fixture = load_fixture("real_dest_city_suggestion.json")
    city_id = _extract_city_id(fixture["result"], "Bangalore")
    assert city_id == 122


def test_extract_city_id_raises_on_empty_docs():
    with pytest.raises(ScrapeError, match="zero matches"):
        _extract_city_id({"ok": True, "parsed": {"response": {"docs": []}}}, "Nowhereville")


def test_extract_city_id_raises_on_http_failure():
    with pytest.raises(ScrapeError, match="HTTP 404"):
        _extract_city_id({"ok": False, "status": 404}, "Chennai")


def test_parse_real_bus_search_response_returns_snapshots():
    fixture = load_fixture("real_bus_search_response.json")
    snapshots = parse_bus_search_response(fixture["result"])

    # We know from inspecting the real data there were 10 inventories
    # in this page of results.
    assert len(snapshots) == 10

    # Spot-check the first real bus we saw: Jai Sai Baba Travels
    matching = [s for s in snapshots.values() if s["operator"] == "Jai Sai Baba Travels"]
    assert len(matching) == 1
    bus = matching[0]
    assert bus["bus_type"] == "A/C Sleeper (2+1)"
    assert bus["available"] == 24
    assert bus["price"] == 1190  # min of fareList [1190, 1240, 1290, 1590, 1690]
    assert bus["departure_time"] == "23:00"  # formatted from "2026-08-15 23:00:00"
    assert bus["arrival_time"] == "05:50 (+1d)"  # real data: arrives next calendar day


def test_arrival_time_same_day_has_no_day_offset_suffix():
    fixture = {
        "ok": True,
        "parsed": {"success": True, "data": {"inventories": [{
            "serviceId": "S1", "travelsName": "Test Line", "busType": "Seater",
            "availableSeats": 5, "fareList": [500],
            "departureTime": "2026-08-15 08:00:00",
            "arrivalTime": "2026-08-15 14:00:00",
        }]}},
    }
    snapshots = parse_bus_search_response(fixture)
    bus = list(snapshots.values())[0]
    assert bus["arrival_time"] == "14:00"  # no "(+Nd)" suffix for same-day arrival


def test_parse_real_response_all_snapshots_have_required_fields():
    fixture = load_fixture("real_bus_search_response.json")
    snapshots = parse_bus_search_response(fixture["result"])
    for key, snap in snapshots.items():
        assert isinstance(snap["operator"], str) and snap["operator"]
        assert isinstance(snap["available"], int)
        assert snap["available"] >= 0


def test_seat_type_filter_sleeper_only():
    fixture = load_fixture("real_bus_search_response.json")
    all_snapshots = parse_bus_search_response(fixture["result"], seat_type="any")
    sleeper_snapshots = parse_bus_search_response(fixture["result"], seat_type="sleeper")
    # All real buses in this fixture happen to be sleeper (route is an
    # overnight Chennai->Bangalore route) - filter should not exclude any
    # of them, and should never exceed the unfiltered count.
    assert len(sleeper_snapshots) <= len(all_snapshots)
    assert len(sleeper_snapshots) > 0


def test_seat_type_filter_seater_excludes_sleeper_buses():
    fixture = load_fixture("real_bus_search_response.json")
    seater_snapshots = parse_bus_search_response(fixture["result"], seat_type="seater")
    # This overnight route fixture is all sleeper buses, so filtering for
    # seater-only should exclude all of them.
    assert len(seater_snapshots) == 0


def test_parse_raises_on_success_false():
    with pytest.raises(ScrapeError, match="success=false"):
        parse_bus_search_response({"ok": True, "parsed": {"success": False}})


def test_parse_raises_on_http_failure():
    with pytest.raises(ScrapeError, match="HTTP 500"):
        parse_bus_search_response({"ok": False, "status": 500})


def test_parse_raises_on_unparseable_body():
    with pytest.raises(ScrapeError, match="no parseable JSON"):
        parse_bus_search_response({"ok": True, "parsed": None, "parseError": "Unexpected token"})


def test_snapshot_keys_are_stable_across_identical_calls():
    """Same input should always produce the same keys - critical for the
    state-diffing logic in state.py to correctly track individual buses
    across separate runs."""
    fixture = load_fixture("real_bus_search_response.json")
    snapshots_1 = parse_bus_search_response(fixture["result"])
    snapshots_2 = parse_bus_search_response(fixture["result"])
    assert set(snapshots_1.keys()) == set(snapshots_2.keys())


def test_results_are_sorted_by_price_ascending():
    fixture = load_fixture("real_bus_search_response.json")
    snapshots = parse_bus_search_response(fixture["result"])
    prices = [s["price"] for s in snapshots.values() if s["price"] is not None]
    assert prices == sorted(prices)


def test_operators_filter_matches_real_operator_name():
    fixture = load_fixture("real_bus_search_response.json")
    all_snapshots = parse_bus_search_response(fixture["result"])
    # "Jai Sai Baba Travels" is a real operator confirmed present in the fixture
    filtered = parse_bus_search_response(fixture["result"], operators=["Jai Sai Baba"])
    assert 0 < len(filtered) < len(all_snapshots)
    assert all("jai sai baba" in s["operator"].lower() for s in filtered.values())


def test_operators_filter_no_match_returns_empty():
    fixture = load_fixture("real_bus_search_response.json")
    filtered = parse_bus_search_response(fixture["result"], operators=["Some Operator That Does Not Exist XYZ"])
    assert filtered == {}


def test_operators_filter_is_case_insensitive():
    fixture = load_fixture("real_bus_search_response.json")
    lower = parse_bus_search_response(fixture["result"], operators=["jai sai baba"])
    upper = parse_bus_search_response(fixture["result"], operators=["JAI SAI BABA"])
    assert set(lower.keys()) == set(upper.keys())
    assert len(lower) > 0


def test_city_ids_included_as_meta_entry_when_provided():
    fixture = load_fixture("real_bus_search_response.json")
    snapshots = parse_bus_search_response(fixture["result"], city_ids={"source": 123, "dest": 122})
    assert "__meta__" in snapshots
    assert snapshots["__meta__"] == {"city_ids": {"source": 123, "dest": 122}}


def test_no_meta_entry_when_city_ids_not_provided():
    fixture = load_fixture("real_bus_search_response.json")
    snapshots = parse_bus_search_response(fixture["result"])
    assert "__meta__" not in snapshots


def test_operator_filter_ignores_spacing_and_punctuation_differences():
    """
    Real-world bug this fixes: a user might write "SNB" while RedBus's
    stored name is "S N B Travels" or "S.N.B. Travels" - a plain substring
    match would miss it entirely.
    """
    fixture = {
        "ok": True,
        "parsed": {"success": True, "data": {"inventories": [{
            "serviceId": "S1", "travelsName": "S N B Travels", "busType": "Sleeper",
            "availableSeats": 5, "fareList": [900],
            "departureTime": "2026-08-15 22:00:00", "arrivalTime": "2026-08-16 05:00:00",
        }]}},
    }
    snapshots = parse_bus_search_response(fixture, operators=["SNB"])
    assert len(snapshots) == 1


def test_operator_filter_ignores_punctuation_variant():
    fixture = {
        "ok": True,
        "parsed": {"success": True, "data": {"inventories": [{
            "serviceId": "S1", "travelsName": "S.N.B. Travels", "busType": "Sleeper",
            "availableSeats": 5, "fareList": [900],
            "departureTime": "2026-08-15 22:00:00", "arrivalTime": "2026-08-16 05:00:00",
        }]}},
    }
    snapshots = parse_bus_search_response(fixture, operators=["S N B"])
    assert len(snapshots) == 1


def test_merge_search_pages_combines_inventories_from_multiple_pages():
    from redbus_watch.scraper_bus import _merge_search_pages

    page1 = {
        "ok": True,
        "parsed": {"success": True, "data": {
            "metaData": {"totalCount": 4},
            "inventories": [
                {"serviceId": "S1", "travelsName": "Op A", "fareList": [500],
                 "departureTime": "2026-08-15 08:00:00", "arrivalTime": "2026-08-15 14:00:00",
                 "availableSeats": 2, "busType": "Seater"},
                {"serviceId": "S2", "travelsName": "Op B", "fareList": [600],
                 "departureTime": "2026-08-15 09:00:00", "arrivalTime": "2026-08-15 15:00:00",
                 "availableSeats": 3, "busType": "Seater"},
            ],
        }},
    }
    page2 = {
        "ok": True,
        "parsed": {"success": True, "data": {
            "metaData": {"totalCount": 4},
            "inventories": [
                {"serviceId": "S3", "travelsName": "Op C", "fareList": [700],
                 "departureTime": "2026-08-15 10:00:00", "arrivalTime": "2026-08-15 16:00:00",
                 "availableSeats": 1, "busType": "Seater"},
                {"serviceId": "S4", "travelsName": "Op D (Mettur)", "fareList": [800],
                 "departureTime": "2026-08-15 11:00:00", "arrivalTime": "2026-08-15 17:00:00",
                 "availableSeats": 4, "busType": "Seater"},
            ],
        }},
    }

    merged = _merge_search_pages([page1, page2])
    snapshots = parse_bus_search_response(merged)

    # All 4 buses from both pages must be present - this is the exact bug
    # class that caused a real operator to be missed when only page 1 was
    # ever fetched.
    assert len(snapshots) == 4
    operators_found = {s["operator"] for s in snapshots.values()}
    assert operators_found == {"Op A", "Op B", "Op C", "Op D (Mettur)"}


def test_merge_search_pages_single_page_unaffected():
    from redbus_watch.scraper_bus import _merge_search_pages
    fixture = load_fixture("real_bus_search_response.json")
    merged = _merge_search_pages([fixture["result"]])
    snapshots = parse_bus_search_response(merged)
    assert len(snapshots) == 10  # identical to the single-page case


def test_merge_search_pages_surfaces_first_page_failure():
    from redbus_watch.scraper_bus import _merge_search_pages
    failed_page = {"ok": False, "status": 500}
    merged = _merge_search_pages([failed_page])
    with pytest.raises(ScrapeError, match="HTTP 500"):
        parse_bus_search_response(merged)
