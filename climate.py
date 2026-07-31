"""Collect daily maximum-temperature normals for the Palm Springs station."""

from __future__ import annotations

import argparse
import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "climate.json"
OUTPUT_DIR = ROOT / ".build" / "climate"
JSON_FILENAME = "daily-max-temperature.json"
CSV_FILENAME = "daily-max-temperature.csv"


def request_session() -> requests.Session:
    """Create a retrying session for the SERCC endpoint."""
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def load_config() -> dict[str, Any]:
    """Load and validate climate collection settings."""
    config = json.loads(CONFIG_PATH.read_text())
    required = {
        "station_id",
        "station_name",
        "variable",
        "timezone",
        "initial_backfill_days",
        "refresh_days",
        "endpoint",
        "source",
        "source_url",
        "public_base_url",
    }
    missing = required - config.keys()
    if missing:
        raise ValueError(f"Climate config is missing: {', '.join(sorted(missing))}")
    return config


def endpoint_params(config: dict[str, Any], valid_date: date) -> dict[str, str]:
    """Build the Climate Perspectives query parameters."""
    return {
        "validdate": valid_date.isoformat(),
        "var": config["variable"],
        "thresh": "",
        "period": "1_DAY",
        "map_display": "value",
        "showthrdx": "true",
        "showcoop": "true",
        "domain": "wrcc",
    }


def parse_number(value: Any) -> float:
    """Parse signed numeric fields such as '+4'."""
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[+-]?\d+(?:\.\d+)?", str(value))
    if not match:
        raise ValueError(f"Could not parse numeric climate value: {value!r}")
    return float(match.group())


def compact_number(value: float) -> int | float:
    """Return integer-valued temperatures as integers."""
    return int(value) if value.is_integer() else round(value, 1)


def station_record(
    config: dict[str, Any],
    valid_date: date,
    payload: dict[str, Any],
    request_url: str,
) -> dict[str, Any] | None:
    """Extract one validated station-day record."""
    station_data = payload.get("data")
    if not isinstance(station_data, dict):
        return None
    station = station_data.get(config["station_id"])
    if not isinstance(station, dict):
        return None

    observed = parse_number(station.get("value"))
    departure = parse_number(station.get("dfn"))
    normal = observed - departure
    label = str(station.get("dfnlabel") or "")
    years_match = re.search(r"Based on (\d+) year", label)
    return {
        "date": valid_date.isoformat(),
        "station_id": station["id"],
        "station_name": station["name"],
        "station_type": station.get("type"),
        "city": station.get("city"),
        "state": station.get("state"),
        "latitude": station.get("lat"),
        "longitude": station.get("lon"),
        "max_temp_f": compact_number(observed),
        "departure_from_normal_f": compact_number(departure),
        "normal_max_temp_f": compact_number(normal),
        "data_pct": station.get("datapct"),
        "rank": station.get("rank"),
        "rank_text": station.get("ranktext"),
        "percentile": station.get("perc"),
        "record_years": int(years_match.group(1)) if years_match else None,
        "source_request_url": request_url,
    }


def fetch_date(config: dict[str, Any], valid_date: date) -> dict[str, Any] | None:
    """Fetch one station day, returning None when it is unavailable."""
    session = request_session()
    response = session.get(
        config["endpoint"],
        params=endpoint_params(config, valid_date),
        timeout=60,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            f"Climate endpoint returned invalid JSON for {valid_date}"
        ) from exc
    return station_record(config, valid_date, payload, response.url)


def load_existing(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Load the published history when one already exists."""
    url = f"{config['public_base_url'].rstrip('/')}/{JSON_FILENAME}"
    response = request_session().get(url, timeout=60)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Published climate JSON has no data array")
    return data


def date_range(end_date: date, days: int) -> list[date]:
    """Return an inclusive ascending date range ending on end_date."""
    start_date = end_date - timedelta(days=days - 1)
    return [start_date + timedelta(days=offset) for offset in range(days)]


def collect_dates(
    config: dict[str, Any], dates: list[date], workers: int = 4
) -> list[dict[str, Any]]:
    """Fetch station days concurrently while keeping output ordered."""
    records = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_date, config, day): day for day in dates}
        for future in as_completed(futures):
            day = futures[future]
            record = future.result()
            if record is None:
                print(f"No station data for {day}")
            else:
                records.append(record)
    return sorted(records, key=lambda record: record["date"])


def merge_records(
    existing: list[dict[str, Any]], updates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replace matching station-days and return a deterministic history."""
    records = {
        (record["date"], record["station_id"]): record for record in existing
    }
    for record in updates:
        records[(record["date"], record["station_id"])] = record
    return [records[key] for key in sorted(records)]


def build_history(
    existing: list[dict[str, Any]], updates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Preserve published history when the station has no new observations."""
    if not existing and not updates:
        raise RuntimeError("No climate observations were available")
    return merge_records(existing, updates)


def write_outputs(config: dict[str, Any], records: list[dict[str, Any]]) -> None:
    """Write matching JSON and CSV history files."""
    if not records:
        raise RuntimeError("Climate history is empty")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload = {
        "metadata": {
            "source": config["source"],
            "source_url": config["source_url"],
            "station_id": config["station_id"],
            "station_name": config["station_name"],
            "units": "degrees Fahrenheit",
            "description": (
                "Observed daily maximum temperature, reported departure and "
                "derived historical normal."
            ),
            "normal_calculation": "max_temp_f - departure_from_normal_f",
            "generated_at": generated_at,
            "start_date": records[0]["date"],
            "end_date": records[-1]["date"],
            "record_count": len(records),
        },
        "data": records,
    }
    (OUTPUT_DIR / JSON_FILENAME).write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n"
    )
    with (OUTPUT_DIR / CSV_FILENAME).open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def main() -> None:
    """Refresh the available daily climate history."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--backfill-days", type=int)
    args = parser.parse_args()

    config = load_config()
    timezone = ZoneInfo(config["timezone"])
    end_date = args.end_date or (datetime.now(timezone).date() - timedelta(days=1))
    existing = load_existing(config)
    days = args.backfill_days or (
        config["refresh_days"] if existing else config["initial_backfill_days"]
    )
    if days < 1:
        raise ValueError("Backfill days must be positive")

    updates = collect_dates(config, date_range(end_date, days))
    if not updates:
        print("No new observations; preserving the published climate history.")
    records = build_history(existing, updates)
    write_outputs(config, records)
    print(
        f"Published {len(records)} station-days from "
        f"{records[0]['date']} through {records[-1]['date']}."
    )


if __name__ == "__main__":
    main()
