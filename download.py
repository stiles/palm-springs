"""Download configured Palm Springs ArcGIS layers and rebuild the inventory."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ezesri
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
SOURCES_PATH = ROOT / "sources.json"
DATA_DIR = ROOT / ".build" / "data"
CATALOG_PATH = DATA_DIR / "catalog.json"
README_PATH = ROOT / "README.md"
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL", "https://stilesdata.com/palm-springs/data"
).rstrip("/")
REQUIRED_SOURCE_FIELDS = {"layer", "url", "source", "source_url"}
LAYER_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def load_sources() -> list[dict[str, str]]:
    """Load and validate the source configuration."""
    sources = json.loads(SOURCES_PATH.read_text())
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources.json must contain a nonempty list")

    seen_layers: set[str] = set()
    seen_urls: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValueError(f"Source {index + 1} must be an object")

        missing = REQUIRED_SOURCE_FIELDS - source.keys()
        if missing:
            raise ValueError(
                f"Source {index + 1} is missing: {', '.join(sorted(missing))}"
            )

        layer = source["layer"]
        url = source["url"]
        if not LAYER_ID_PATTERN.fullmatch(layer):
            raise ValueError(f"Invalid layer ID {layer!r}; use lowercase kebab-case")
        if layer in seen_layers:
            raise ValueError(f"Duplicate layer ID: {layer}")
        if url in seen_urls:
            raise ValueError(f"Duplicate layer URL: {url}")

        seen_layers.add(layer)
        seen_urls.add(url)

    return sources


def plain_text(value: Any) -> str:
    """Convert Esri HTML metadata into compact plain text."""
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", html.unescape(str(value)))
    return re.sub(r"\s+", " ", text).strip()


def iso_from_milliseconds(value: Any) -> str | None:
    """Convert an Esri millisecond timestamp to UTC ISO 8601."""
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()


def source_last_updated(metadata: dict[str, Any]) -> str | None:
    """Read the most useful edit timestamp exposed by an Esri layer."""
    editing_info = metadata.get("editingInfo") or {}
    value = editing_info.get("lastEditDate")
    if value is None:
        value = metadata.get("lastEditDate")
    return iso_from_milliseconds(value)


def geometry_label(metadata: dict[str, Any], geodataframe: Any) -> str:
    """Return a readable geometry type."""
    geometry_type = metadata.get("geometryType")
    labels = {
        "esriGeometryPoint": "Point",
        "esriGeometryMultipoint": "MultiPoint",
        "esriGeometryPolyline": "LineString",
        "esriGeometryPolygon": "Polygon",
    }
    if geometry_type in labels:
        return labels[geometry_type]
    if not geodataframe.empty:
        return ", ".join(sorted(geodataframe.geometry.geom_type.unique()))
    return "Unknown"


def expected_feature_count(url: str) -> int:
    """Fetch the source count with retries so partial downloads are rejected."""
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    response = session.get(
        f"{url}/query",
        params={"f": "json", "where": "1=1", "returnCountOnly": "true"},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload or not isinstance(payload.get("count"), int):
        raise RuntimeError(f"Could not read feature count for {url}: {payload}")
    return payload["count"]


def download_layer(
    source: dict[str, str], output_dir: Path, generated_at: str
) -> dict[str, Any]:
    """Download one layer as WGS84 GeoJSON and return its catalog entry."""
    layer_id = source["layer"]
    print(f"Downloading {layer_id}...")

    metadata = ezesri.get_metadata(source["url"])
    if not isinstance(metadata, dict) or not metadata.get("geometryType"):
        raise RuntimeError(f"{source['url']} is not a readable spatial layer")

    expected_count = expected_feature_count(source["url"])
    geodataframe = ezesri.extract_layer(source["url"])
    if len(geodataframe) != expected_count:
        raise RuntimeError(
            f"{layer_id}: expected {expected_count:,} features, "
            f"downloaded {len(geodataframe):,}"
        )
    if geodataframe.crs is not None and geodataframe.crs.to_epsg() != 4326:
        geodataframe = geodataframe.to_crs(epsg=4326)

    object_id_field = metadata.get("objectIdField")
    if object_id_field in geodataframe.columns:
        geodataframe = geodataframe.sort_values(object_id_field)

    output_path = output_dir / f"{layer_id}.geojson"
    geodataframe.to_file(output_path, driver="GeoJSON", index=False)

    description = plain_text(source.get("description"))
    if not description:
        description = plain_text(metadata.get("description"))
    if not description:
        description = plain_text(metadata.get("serviceDescription"))

    return {
        "id": layer_id,
        "title": plain_text(source.get("title"))
        or plain_text(metadata.get("name"))
        or layer_id,
        "description": description,
        "feature_count": len(geodataframe),
        "geometry_type": geometry_label(metadata, geodataframe),
        "crs": "EPSG:4326",
        "source": source["source"],
        "source_url": source["source_url"],
        "service_url": source["url"],
        "data_url": f"{PUBLIC_BASE_URL}/{layer_id}.geojson",
        "source_definition_query": metadata.get("definitionQuery") or None,
        "source_last_updated": source_last_updated(metadata),
        "downloaded_at": generated_at,
    }


def markdown_cell(value: Any) -> str:
    """Escape text for a Markdown table cell."""
    return str(value or "—").replace("|", r"\|").replace("\n", " ")


def build_readme(catalog: dict[str, Any]) -> str:
    """Build the repository documentation and layer inventory."""
    rows = []
    for layer in catalog["layers"]:
        updated = layer["source_last_updated"]
        updated_date = updated[:10] if updated else "Not reported"
        rows.append(
            "| [{title}]({data_url}) | {description} | {count:,} | "
            "{geometry} | {updated} | [ArcGIS]({service_url}) |".format(
                title=markdown_cell(layer["title"]),
                data_url=layer["data_url"],
                description=markdown_cell(layer["description"]),
                count=layer["feature_count"],
                geometry=markdown_cell(layer["geometry_type"]),
                updated=updated_date,
                service_url=layer["service_url"],
            )
        )

    generated_date = catalog["generated_at"][:10]
    inventory = "\n".join(rows)
    return f"""# Palm Springs open data

Current copies of public GIS layers published by the City of Palm Springs,
California. The data is downloaded from ArcGIS once a week and converted to
GeoJSON in WGS84 (EPSG:4326).

## Layers

Inventory generated {generated_date}. Feature counts describe the files on S3.
“Source updated” is the edit date reported by ArcGIS when available.

| Layer | Description | Features | Geometry | Source updated | Service |
| --- | --- | ---: | --- | --- | --- |
{inventory}

Machine-readable metadata is available in
[`catalog.json`]({PUBLIC_BASE_URL}/catalog.json).

## Update the data

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make update
```

Edit [`sources.json`](sources.json) to add or remove layers. Layer IDs must be
unique lowercase kebab-case values. A failed download exits without replacing
the existing data.

Updates upload to `s3://stilesdata.com/palm-springs/data/`. If
`AWS_PROFILE_NAME` is set, the uploader uses that AWS profile; otherwise it uses
the default AWS credential chain. Override `BUCKET` or `PREFIX` when needed.

The [weekly workflow](.github/workflows/update-data.yml) runs every Monday,
uploads the current files to S3 and refreshes this inventory. It requires
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` repository secrets and can also
be run manually from the Actions tab.

## Source and reuse

The City of Palm Springs is the source of these layers. This repository
republishes snapshots on S3 for convenience and does not alter source attributes
beyond converting coordinates to WGS84. Consult the linked ArcGIS services for
authoritative data, descriptions and applicable use terms.
"""


def publish(staging_dir: Path, catalog: dict[str, Any]) -> None:
    """Replace generated files only after every layer succeeds."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    expected_files = {"catalog.json"}
    for layer in catalog["layers"]:
        filename = f"{layer['id']}.geojson"
        expected_files.add(filename)
        shutil.move(staging_dir / filename, DATA_DIR / filename)

    for existing_path in DATA_DIR.glob("*.geojson"):
        if existing_path.name not in expected_files:
            existing_path.unlink()

    CATALOG_PATH.write_text(json.dumps(catalog, indent=2) + "\n")
    README_PATH.write_text(build_readme(catalog))


def main() -> None:
    """Download all configured layers and publish the complete snapshot."""
    sources = load_sources()
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()

    with tempfile.TemporaryDirectory(prefix="palm-springs-") as temp_dir:
        staging_dir = Path(temp_dir)
        layers = [
            download_layer(source, staging_dir, generated_at) for source in sources
        ]
        catalog = {
            "generated_at": generated_at,
            "crs": "EPSG:4326",
            "layers": layers,
        }
        publish(staging_dir, catalog)

    total_features = sum(layer["feature_count"] for layer in layers)
    print(f"Published {len(layers)} layers with {total_features:,} features.")


if __name__ == "__main__":
    main()
