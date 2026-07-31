import json
from datetime import date

import pytest

import climate


def config() -> dict:
    return {
        "station_id": "048892",
        "station_name": "Thermal Fcwos",
        "source": "SERCC",
        "source_url": "https://example.com/about",
    }


def station_payload() -> dict:
    return {
        "data": {
            "048892": {
                "id": "048892",
                "lat": 33.63166,
                "lon": -116.16412,
                "name": "Thermal Fcwos",
                "type": "COOP",
                "city": "Palm Springs",
                "state": "CA",
                "value": 111,
                "datapct": 100,
                "rank": "17*",
                "ranktext": "T-17th warmest",
                "perc": 78,
                "dfnlabel": "* Based on 77 year periods at this station",
                "dfn": "+4",
            }
        }
    }


def test_station_record_derives_daily_normal():
    record = climate.station_record(
        config(),
        date(2026, 7, 10),
        station_payload(),
        "https://example.com/request",
    )

    assert record is not None
    assert record["max_temp_f"] == 111
    assert record["departure_from_normal_f"] == 4
    assert record["normal_max_temp_f"] == 107
    assert record["record_years"] == 77


def test_station_record_skips_unavailable_day():
    assert (
        climate.station_record(
            config(), date(2026, 1, 1), {"data": []}, "https://example.com"
        )
        is None
    )


def test_merge_records_replaces_corrected_station_day():
    old = {"date": "2026-07-10", "station_id": "048892", "max_temp_f": 110}
    corrected = {"date": "2026-07-10", "station_id": "048892", "max_temp_f": 111}

    assert climate.merge_records([old], [corrected]) == [corrected]


def test_build_history_preserves_existing_data_during_station_outage():
    existing = [{"date": "2026-07-22", "station_id": "048892"}]

    assert climate.build_history(existing, []) == existing


def test_build_history_rejects_empty_initial_collection():
    with pytest.raises(RuntimeError, match="No climate observations"):
        climate.build_history([], [])


def test_write_outputs_creates_matching_json_and_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(climate, "OUTPUT_DIR", tmp_path)
    record = climate.station_record(
        config(),
        date(2026, 7, 10),
        station_payload(),
        "https://example.com/request",
    )
    assert record is not None

    climate.write_outputs(config(), [record])

    payload = json.loads((tmp_path / climate.JSON_FILENAME).read_text())
    assert payload["metadata"]["record_count"] == 1
    assert payload["data"][0]["normal_max_temp_f"] == 107
    csv_text = (tmp_path / climate.CSV_FILENAME).read_text()
    assert "normal_max_temp_f" in csv_text
    assert "2026-07-10" in csv_text
