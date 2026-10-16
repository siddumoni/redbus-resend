import pytest

from redbus_watch.scraper_train import ScrapeError, parse_train_search_response


def test_parses_explicit_train_availability():
    payload = {
        "data": {
            "trains": [{
                "trainNo": "12657",
                "trainName": "Chennai Mail",
                "departureTime": "22:50",
                "arrivalTime": "06:20",
                "class": "SL",
                "availabilityStatus": "AVAILABLE 12",
            }]
        }
    }
    result = parse_train_search_response(payload)
    assert "12657_sl" in result
    assert result["12657_sl"]["train_name"] == "Chennai Mail"
    assert result["12657_sl"]["available"] == 12
    assert result["12657_sl"]["status_text"] == "AVAILABLE 12"


def test_parses_rac_as_bookable_signal():
    payload = {"trains": [{
        "trainNumber": "12007",
        "name": "Shatabdi",
        "classCode": "CC",
        "status": "RAC 4",
    }]}
    result = parse_train_search_response(payload)
    assert result["12007_cc"]["available"] == 1
    assert result["12007_cc"]["status_text"] == "RAC 4"


def test_class_filter():
    payload = {"trains": [
        {"trainNo": "12657", "trainName": "Chennai Mail", "class": "SL", "status": "AVAILABLE 10"},
        {"trainNo": "12657", "trainName": "Chennai Mail", "class": "3A", "status": "WL 4"},
    ]}
    result = parse_train_search_response(payload, ["SL"])
    assert list(result) == ["12657_sl"]


def test_unknown_payload_fails_closed():
    with pytest.raises(ScrapeError, match="No recognizable train inventory"):
        parse_train_search_response({"success": True, "data": {"message": "ok"}})


def test_empty_json_is_not_assumed_to_mean_no_availability():
    with pytest.raises(ScrapeError):
        parse_train_search_response({"trains": []})


def test_waitlist_is_not_counted_as_available():
    payload = {"trains": [{
        "trainNo": "12657",
        "trainName": "Chennai Mail",
        "class": "SL",
        "status": "WL 12",
    }]}
    result = parse_train_search_response(payload)
    assert result["12657_sl"]["available"] == 0
    assert result["12657_sl"]["status_text"] == "WL 12"
