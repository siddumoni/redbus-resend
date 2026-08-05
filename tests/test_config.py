import json
import pytest
from redbus_watch.config import parse_watches, ConfigError


def test_parses_valid_bus_and_train_watches():
    raw = json.dumps([
        {"type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15"},
        {"type": "train", "source": "MAS", "destination": "SBC", "date": "2026-08-20", "classes": ["sl", "3a"]},
    ])
    watches = parse_watches(raw)
    assert len(watches) == 2
    assert watches[0].type == "bus"
    assert watches[0].label == "Chennai \u2192 Bangalore (2026-08-15)"
    assert watches[1].classes == ["SL", "3A"]


def test_empty_env_raises():
    with pytest.raises(ConfigError):
        parse_watches("")


def test_invalid_json_raises():
    with pytest.raises(ConfigError):
        parse_watches("{not valid json")


def test_not_a_list_raises():
    with pytest.raises(ConfigError):
        parse_watches(json.dumps({"type": "bus"}))


def test_empty_list_raises():
    with pytest.raises(ConfigError):
        parse_watches(json.dumps([]))


def test_missing_required_field_raises():
    raw = json.dumps([{"type": "bus", "source": "Chennai", "date": "2026-08-15"}])
    with pytest.raises(ConfigError, match="destination"):
        parse_watches(raw)


def test_invalid_type_raises():
    raw = json.dumps([{"type": "flight", "source": "A", "destination": "B", "date": "2026-08-15"}])
    with pytest.raises(ConfigError, match="invalid type"):
        parse_watches(raw)


def test_invalid_seat_type_raises():
    raw = json.dumps([{
        "type": "bus", "source": "A", "destination": "B", "date": "2026-08-15", "seat_type": "luxury"
    }])
    with pytest.raises(ConfigError, match="invalid seat_type"):
        parse_watches(raw)


def test_duplicate_watch_id_raises():
    entry = {"type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15"}
    raw = json.dumps([entry, dict(entry)])
    with pytest.raises(ConfigError, match="Duplicate watch"):
        parse_watches(raw)


def test_custom_label_is_used():
    raw = json.dumps([{
        "type": "bus", "source": "Chennai", "destination": "Bangalore",
        "date": "2026-08-15", "label": "My Trip"
    }])
    watches = parse_watches(raw)
    assert watches[0].label == "My Trip"


def test_watch_id_is_stable_and_distinguishes_classes():
    raw = json.dumps([
        {"type": "train", "source": "MAS", "destination": "SBC", "date": "2026-08-20", "classes": ["SL"]},
        {"type": "train", "source": "MAS", "destination": "SBC", "date": "2026-08-20", "classes": ["3A"]},
    ])
    watches = parse_watches(raw)
    assert watches[0].watch_id() != watches[1].watch_id()


def test_dates_list_expands_into_multiple_watches():
    raw = json.dumps([{
        "type": "bus", "source": "Chennai", "destination": "Coimbatore",
        "dates": ["2026-08-15", "2026-08-16", "2026-08-17"],
    }])
    watches = parse_watches(raw)
    assert len(watches) == 3
    assert [w.date for w in watches] == ["2026-08-15", "2026-08-16", "2026-08-17"]
    # each expanded watch must have a distinct watch_id
    assert len({w.watch_id() for w in watches}) == 3


def test_dates_list_with_custom_label_appends_date_for_uniqueness():
    raw = json.dumps([{
        "type": "bus", "source": "Chennai", "destination": "Coimbatore",
        "dates": ["2026-08-15", "2026-08-16"], "label": "CBE trip",
    }])
    watches = parse_watches(raw)
    assert watches[0].label == "CBE trip (2026-08-15)"
    assert watches[1].label == "CBE trip (2026-08-16)"


def test_single_date_in_dates_list_keeps_plain_label():
    raw = json.dumps([{
        "type": "bus", "source": "Chennai", "destination": "Coimbatore",
        "dates": ["2026-08-15"], "label": "CBE trip",
    }])
    watches = parse_watches(raw)
    assert watches[0].label == "CBE trip"


def test_both_date_and_dates_raises():
    raw = json.dumps([{
        "type": "bus", "source": "Chennai", "destination": "Coimbatore",
        "date": "2026-08-15", "dates": ["2026-08-16"],
    }])
    with pytest.raises(ConfigError, match="either 'date' or 'dates'"):
        parse_watches(raw)


def test_neither_date_nor_dates_raises():
    raw = json.dumps([{"type": "bus", "source": "Chennai", "destination": "Coimbatore"}])
    with pytest.raises(ConfigError, match="date"):
        parse_watches(raw)


def test_empty_dates_list_raises():
    raw = json.dumps([{"type": "bus", "source": "Chennai", "destination": "Coimbatore", "dates": []}])
    with pytest.raises(ConfigError, match="empty list"):
        parse_watches(raw)


def test_operators_filter_parsed_and_normalized():
    raw = json.dumps([{
        "type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15",
        "operators": ["  SRS Travels ", "KPN"],
    }])
    watches = parse_watches(raw)
    assert watches[0].operators == ["SRS Travels", "KPN"]


def test_operators_must_be_list_of_strings():
    raw = json.dumps([{
        "type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15",
        "operators": "SRS Travels",
    }])
    with pytest.raises(ConfigError, match="'operators' must be a list"):
        parse_watches(raw)


def test_empty_operators_list_treated_as_no_filter():
    raw = json.dumps([{
        "type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15",
        "operators": [],
    }])
    watches = parse_watches(raw)
    assert watches[0].operators is None


def test_operators_affects_watch_id_uniqueness():
    raw = json.dumps([
        {"type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15",
         "operators": ["SRS"]},
        {"type": "bus", "source": "Chennai", "destination": "Bangalore", "date": "2026-08-15",
         "operators": ["KPN"]},
    ])
    watches = parse_watches(raw)
    assert watches[0].watch_id() != watches[1].watch_id()


def test_dates_and_operators_together_produce_correct_count_and_unique_ids():
    raw = json.dumps([{
        "type": "bus", "source": "Chennai", "destination": "Bangalore",
        "dates": ["2026-08-15", "2026-08-16"], "operators": ["SRS Travels"],
    }])
    watches = parse_watches(raw)
    assert len(watches) == 2
    assert len({w.watch_id() for w in watches}) == 2
    assert all(w.operators == ["SRS Travels"] for w in watches)
