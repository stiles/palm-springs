"""Download Palm Springs GIS layers, derive datasets, and rebuild the inventory."""

from __future__ import annotations

import csv
import gzip
import hashlib
import html
import io
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import ezesri
import geopandas as gpd
import mercantile
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union
from urllib3.util.retry import Retry

from census import build_census_outputs, load_census_config

ROOT = Path(__file__).resolve().parent
SOURCES_PATH = ROOT / "sources.json"
DERIVED_SOURCES_PATH = ROOT / "derived-sources.json"
CENSUS_CONFIG_PATH = ROOT / "census.json"
DATA_DIR = ROOT / ".build" / "data"
CATALOG_PATH = DATA_DIR / "catalog.json"
README_PATH = ROOT / "README.md"
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL", "https://stilesdata.com/palm-springs/data"
).rstrip("/")
REQUIRED_SOURCE_FIELDS = {"layer", "url", "source", "source_url"}
REQUIRED_DERIVED_FIELDS = {
    "type",
    "layer",
    "title",
    "description",
    "source",
    "source_url",
    "inputs",
}
DERIVED_TYPE_FIELDS = {
    "building-footprints": {
        "manifest_url",
        "location",
        "zoom",
        "license",
        "license_url",
        "license_file",
    },
    "static-vector": {
        "archive_url",
        "archive_layer",
        "source_last_updated",
        "source_crs",
        "expected_feature_count",
        "contact",
        "disclaimer",
    },
}
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


def load_derived_sources() -> list[dict[str, Any]]:
    """Load and validate derived-layer configuration."""
    sources = json.loads(DERIVED_SOURCES_PATH.read_text())
    if not isinstance(sources, list):
        raise ValueError("derived-sources.json must contain a list")

    seen_layers: set[str] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValueError(f"Derived source {index + 1} must be an object")
        source_type = source.get("type")
        type_fields = DERIVED_TYPE_FIELDS.get(source_type)
        if type_fields is None:
            raise ValueError(f"Unknown derived layer type: {source_type!r}")
        missing = (REQUIRED_DERIVED_FIELDS | type_fields) - source.keys()
        if missing:
            raise ValueError(
                f"Derived source {index + 1} is missing: "
                f"{', '.join(sorted(missing))}"
            )
        layer = source["layer"]
        if not isinstance(layer, str) or not LAYER_ID_PATTERN.fullmatch(layer):
            raise ValueError(f"Invalid derived layer ID: {layer!r}")
        if layer in seen_layers:
            raise ValueError(f"Duplicate derived layer ID: {layer}")
        seen_layers.add(layer)

    return sources


def request_session() -> requests.Session:
    """Create a retrying HTTP session for public data sources."""
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


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
    session = request_session()
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
        "layer_type": "source",
        "source": source["source"],
        "source_url": source["source_url"],
        "source_link_label": "ArcGIS",
        "service_url": source["url"],
        "data_url": f"{PUBLIC_BASE_URL}/{layer_id}.geojson",
        "source_definition_query": metadata.get("definitionQuery") or None,
        "source_last_updated": source_last_updated(metadata),
        "downloaded_at": generated_at,
    }


def select_manifest_tiles(
    manifest_text: str,
    bounds: tuple[float, float, float, float],
    location: str,
    zoom: int,
) -> list[dict[str, str]]:
    """Select all GlobalML manifest tiles covering a WGS84 bounding box."""
    quadkeys = {
        mercantile.quadkey(tile)
        for tile in mercantile.tiles(*bounds, zooms=zoom)
    }
    rows = csv.DictReader(io.StringIO(manifest_text))
    matches = {
        row["QuadKey"]: row
        for row in rows
        if row.get("Location") == location and row.get("QuadKey") in quadkeys
    }
    missing = quadkeys - matches.keys()
    if missing:
        raise RuntimeError(
            f"GlobalML manifest is missing {location} tiles: "
            f"{', '.join(sorted(missing))}"
        )
    return [matches[quadkey] for quadkey in sorted(matches)]


def polygonal_geometry(geometry: Any) -> Polygon | MultiPolygon | None:
    """Return only polygonal parts of an intersection result."""
    if geometry.is_empty:
        return None
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    polygons = [
        part
        for part in getattr(geometry, "geoms", ())
        if isinstance(part, (Polygon, MultiPolygon)) and not part.is_empty
    ]
    if not polygons:
        return None
    combined = unary_union(polygons)
    return combined if isinstance(combined, (Polygon, MultiPolygon)) else None


def stable_building_id(geometry: Polygon | MultiPolygon) -> str:
    """Generate a deterministic ID from normalized clipped geometry."""
    normalized = geometry.normalize()
    digest = hashlib.sha256(normalized.wkb).hexdigest()
    return f"msft-{digest[:24]}"


def optional_number(value: Any) -> float | None:
    """Return a JSON-safe source number, treating GlobalML -1 as unknown."""
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return None if number == -1 else number


def stream_clipped_footprints(
    tile_rows: Iterable[dict[str, str]],
    boundary: Polygon | MultiPolygon,
    session: requests.Session,
) -> gpd.GeoDataFrame:
    """Stream GlobalML tiles and retain footprints clipped to the city."""
    buildings: dict[str, dict[str, Any]] = {}
    for tile in tile_rows:
        print(f"Downloading Microsoft building tile {tile['QuadKey']}...")
        with session.get(tile["Url"], stream=True, timeout=(30, 180)) as response:
            response.raise_for_status()
            with gzip.GzipFile(fileobj=response.raw) as compressed:
                for raw_line in compressed:
                    feature = json.loads(raw_line)
                    footprint = shape(feature["geometry"])
                    if footprint.is_empty or not footprint.intersects(boundary):
                        continue
                    clipped = polygonal_geometry(footprint.intersection(boundary))
                    if clipped is None:
                        continue
                    building_id = stable_building_id(clipped)
                    properties = feature.get("properties") or {}
                    buildings[building_id] = {
                        "building_id": building_id,
                        "height": optional_number(properties.get("height")),
                        "confidence": optional_number(properties.get("confidence")),
                        "geometry": clipped,
                    }

    if not buildings:
        raise RuntimeError("No Microsoft building footprints intersect Palm Springs")
    records = [buildings[key] for key in sorted(buildings)]
    return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")


def json_value(value: Any) -> Any:
    """Convert a scalar dataframe value to a JSON-safe Python value."""
    if value is None or pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def enrich_buildings(
    buildings: gpd.GeoDataFrame, addresses: gpd.GeoDataFrame
) -> list[dict[str, Any]]:
    """Attach strictly contained address records to building polygons."""
    if buildings.crs != addresses.crs:
        addresses = addresses.to_crs(buildings.crs)

    address_fields = [
        "AddressID",
        "Address",
        "Parcel_APN",
        "Unit",
        "ZipCode",
        "Neighborhood",
    ]
    missing_fields = set(address_fields) - set(addresses.columns)
    if missing_fields:
        raise RuntimeError(
            f"Address layer is missing fields: {', '.join(sorted(missing_fields))}"
        )

    matches = gpd.sjoin(
        addresses[address_fields + ["geometry"]],
        buildings[["building_id", "geometry"]],
        how="inner",
        predicate="within",
    )
    addresses_by_building: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for _, address in matches.iterrows():
        addresses_by_building[address["building_id"]].append(
            {field: json_value(address[field]) for field in address_fields}
        )

    features = []
    for building in buildings.sort_values("building_id").itertuples():
        contained = addresses_by_building.get(building.building_id, [])
        contained.sort(
            key=lambda address: (
                str(address["AddressID"] or ""),
                str(address["Address"] or ""),
            )
        )
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "building_id": building.building_id,
                    "height": json_value(building.height),
                    "confidence": json_value(building.confidence),
                    "address_count": len(contained),
                    "addresses": contained,
                },
                "geometry": mapping(building.geometry.normalize()),
            }
        )
    return features


def write_feature_collection(path: Path, features: list[dict[str, Any]]) -> None:
    """Write deterministic GeoJSON while preserving nested address arrays."""
    collection = {
        "type": "FeatureCollection",
        "name": path.stem,
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
        },
        "features": features,
    }
    path.write_text(
        json.dumps(collection, separators=(",", ":"), allow_nan=False) + "\n"
    )


def derive_building_footprints(
    config: dict[str, Any],
    staging_dir: Path,
    generated_at: str,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Build the clipped and address-enriched Microsoft footprint layer."""
    print(f"Deriving {config['layer']}...")
    session = session or request_session()
    boundary_frame = gpd.read_file(staging_dir / "city-boundary.geojson").to_crs(
        "EPSG:4326"
    )
    addresses = gpd.read_file(staging_dir / "addresses.geojson").to_crs("EPSG:4326")
    boundary = polygonal_geometry(boundary_frame.geometry.union_all())
    if boundary is None:
        raise RuntimeError("City boundary does not contain polygon geometry")

    manifest_response = session.get(config["manifest_url"], timeout=60)
    manifest_response.raise_for_status()
    tiles = select_manifest_tiles(
        manifest_response.text,
        tuple(boundary.bounds),
        config["location"],
        config["zoom"],
    )
    buildings = stream_clipped_footprints(tiles, boundary, session)
    features = enrich_buildings(buildings, addresses)
    output_path = staging_dir / f"{config['layer']}.geojson"
    write_feature_collection(output_path, features)

    license_source = ROOT / config["license_file"]
    license_filename = f"{config['layer']}-license.txt"
    shutil.copyfile(license_source, staging_dir / license_filename)
    upload_dates = [tile["UploadDate"] for tile in tiles if tile.get("UploadDate")]
    source_date = max(upload_dates) if upload_dates else None

    return {
        "id": config["layer"],
        "title": config["title"],
        "description": config["description"],
        "feature_count": len(features),
        "geometry_type": "Polygon",
        "crs": "EPSG:4326",
        "layer_type": "derived",
        "source": config["source"],
        "source_url": config["source_url"],
        "source_link_label": "Microsoft",
        "data_url": f"{PUBLIC_BASE_URL}/{config['layer']}.geojson",
        "source_last_updated": source_date,
        "downloaded_at": generated_at,
        "derived_from": config["inputs"],
        "method": "clip-to-city-boundary; attach strictly contained addresses",
        "license": config["license"],
        "license_url": config["license_url"],
        "license_data_url": f"{PUBLIC_BASE_URL}/{license_filename}",
        "source_tiles": [
            {
                "quadkey": tile["QuadKey"],
                "url": tile["Url"],
                "size": tile.get("Size"),
                "upload_date": tile.get("UploadDate"),
            }
            for tile in tiles
        ],
    }


def derive_static_vector(
    config: dict[str, Any],
    staging_dir: Path,
    generated_at: str,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Convert an archived public-record vector layer to web-ready formats."""
    print(f"Converting {config['layer']}...")
    session = session or request_session()
    response = session.get(config["archive_url"], timeout=120)
    response.raise_for_status()
    archive_path = staging_dir / f"{config['layer']}-source.zip"
    archive_path.write_bytes(response.content)

    frame = gpd.read_file(
        f"zip://{archive_path}!{config['archive_layer']}"
    )
    if frame.crs is None or frame.crs.to_string() != config["source_crs"]:
        raise RuntimeError(
            f"{config['layer']} CRS changed: expected {config['source_crs']}, "
            f"found {frame.crs}"
        )
    if len(frame) != config["expected_feature_count"]:
        raise RuntimeError(
            f"{config['layer']} feature count changed: expected "
            f"{config['expected_feature_count']:,}, found {len(frame):,}"
        )
    if frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise RuntimeError(f"{config['layer']} contains missing or empty geometry")
    if not frame.geometry.is_valid.all():
        raise RuntimeError(f"{config['layer']} contains invalid geometry")

    frame = frame.to_crs("EPSG:4326")
    geojson_filename = f"{config['layer']}.geojson"
    parquet_filename = f"{config['layer']}.parquet"
    frame.to_file(staging_dir / geojson_filename, driver="GeoJSON", index=False)
    frame.to_parquet(staging_dir / parquet_filename, index=False)

    return {
        "id": config["layer"],
        "title": config["title"],
        "description": config["description"],
        "feature_count": len(frame),
        "geometry_type": ", ".join(sorted(frame.geometry.geom_type.unique())),
        "crs": "EPSG:4326",
        "layer_type": "derived",
        "source": config["source"],
        "source_url": config["source_url"],
        "source_link_label": "City CPRA release",
        "data_url": f"{PUBLIC_BASE_URL}/{geojson_filename}",
        "source_last_updated": config["source_last_updated"],
        "downloaded_at": generated_at,
        "derived_from": config["inputs"],
        "method": f"reproject {config['source_crs']} to EPSG:4326",
        "source_crs": config["source_crs"],
        "source_archive_url": config["archive_url"],
        "contact": config["contact"],
        "disclaimer": config["disclaimer"],
        "artifacts": [
            {
                "filename": geojson_filename,
                "format": "GeoJSON",
                "url": f"{PUBLIC_BASE_URL}/{geojson_filename}",
            },
            {
                "filename": parquet_filename,
                "format": "GeoParquet",
                "url": f"{PUBLIC_BASE_URL}/{parquet_filename}",
            },
        ],
    }


def derive_layer(
    config: dict[str, Any], staging_dir: Path, generated_at: str
) -> dict[str, Any]:
    """Dispatch a configured derived layer to its implementation."""
    derivers = {
        "building-footprints": derive_building_footprints,
        "static-vector": derive_static_vector,
    }
    derive = derivers.get(config["type"])
    if derive is None:
        raise ValueError(f"Unknown derived layer type: {config['type']}")
    return derive(config, staging_dir, generated_at)


def markdown_cell(value: Any) -> str:
    """Escape text for a Markdown table cell."""
    return str(value or "—").replace("|", r"\|").replace("\n", " ")


def build_readme(catalog: dict[str, Any]) -> str:
    """Build the repository documentation and layer inventory."""
    rows = []
    for layer in catalog["layers"]:
        updated = layer["source_last_updated"]
        updated_date = updated[:10] if updated else "Not reported"
        source_url = layer.get("service_url") or layer["source_url"]
        rows.append(
            "| [{title}]({data_url}) | {description} | {count:,} | "
            "{geometry} | {updated} | [{source_label}]({source_url}) |".format(
                title=markdown_cell(layer["title"]),
                data_url=layer["data_url"],
                description=markdown_cell(layer["description"]),
                count=layer["feature_count"],
                geometry=markdown_cell(layer["geometry_type"]),
                updated=updated_date,
                source_label=markdown_cell(layer["source_link_label"]),
                source_url=source_url,
            )
        )

    generated_date = catalog["generated_at"][:10]
    inventory = "\n".join(rows)
    derived_sections = []
    for layer in catalog["layers"]:
        if layer["layer_type"] != "derived":
            continue
        inputs = ", ".join(f"`{item}`" for item in layer["derived_from"])
        if layer.get("census_vintage"):
            section = (
                "### {title}\n\n"
                "This estimate starts with [{source}]({source_url}) block counts. "
                "We combine them with {inputs}, using addresses and buildings to "
                "place people more realistically than a simple land-area split."
            ).format(
                title=layer["title"],
                source=layer["source"],
                source_url=layer["source_url"],
                inputs=inputs,
            )
        elif layer["id"] == "building-footprints":
            section = (
                "### {title}\n\n"
                "We start with [{source}]({source_url}), clip the footprints to the "
                "city boundary and attach any city address points that fall inside "
                "each building."
            ).format(
                title=layer["title"],
                source=layer["source"],
                source_url=layer["source_url"],
            )
        elif layer["id"] == "city-building-footprints":
            section = (
                "### {title}\n\n"
                "The City of Palm Springs GIS department provided this shapefile "
                "on July 29, 2026 in response to a California Public Records Act "
                "request. The original data uses {source_crs}; this project "
                "reprojects it to WGS84 for the web-ready copies. Download the "
                "[original shapefile archive]({archive_url}) or contact "
                "[{contact}](mailto:{contact}).\n\n"
                "City disclaimer: {disclaimer}"
            ).format(
                title=layer["title"],
                source_crs=layer["source_crs"],
                archive_url=layer["source_archive_url"],
                contact=layer["contact"],
                disclaimer=layer["disclaimer"],
            )
        else:
            section = (
                "### {title}\n\n"
                "This layer combines [{source}]({source_url}) with {inputs}. "
                "The pipeline uses this method: {method}."
            ).format(
                title=layer["title"],
                source=layer["source"],
                source_url=layer["source_url"],
                inputs=inputs,
                method=layer["method"],
            )
        if layer.get("license"):
            section += (
                " The source data is available under "
                "[{license}]({license_url}); a [copy of the license]"
                "({license_data_url}) travels with the output.".format(
                    license=layer["license"],
                    license_url=layer["license_url"],
                    license_data_url=layer["license_data_url"],
                )
            )
        artifacts = layer.get("artifacts", [])
        if artifacts:
            links = " | ".join(
                f"[{artifact['format']}]({artifact['url']})"
                for artifact in artifacts
            )
            section += f"\n\nDownload it as {links}."
        if layer.get("census_vintage"):
            source_total = layer["qa"]["source_totals"]["pop_total"]
            unassigned = layer["qa"]["unassigned_totals"]["pop_total"]
            unassigned_pct = unassigned / source_total * 100 if source_total else 0
            assigned = layer["qa"]["assigned_population"]
            official = layer["qa"]["official_place_population"]
            difference = layer["qa"]["place_population_difference"]
            difference_pct = layer["qa"]["place_population_difference_pct"]
            section += (
                f"\n\nThe {layer['census_vintage']} Census produced an apportioned "
                f"population of {assigned:,} here. The official Palm Springs count "
                f"was {official:,}, a difference of {difference:+,} "
                f"({difference_pct:+.2f}%). Another {unassigned:,} people from "
                f"intersecting blocks remain unassigned ({unassigned_pct:.2f}%) "
                "rather than being forced into a boundary."
            )
        derived_sections.append(section)
    derived_documentation = "\n\n".join(derived_sections)
    derived_section = (
        f"\n\n## Derived layers\n\n{derived_documentation}"
        if derived_documentation
        else ""
    )
    return f"""# Palm Springs GIS data

This repository keeps a current collection of public GIS and climate data for
Palm Springs, California. Most layers come straight from the city. A few take
extra work: the pipeline clips Microsoft building footprints to the city, uses
Census blocks to estimate population for local neighborhoods and voting
precincts and derives a daily maximum-temperature normal from station reports.

Spatial layers refresh once a week. Climate observations refresh daily. Spatial
files use WGS84 (EPSG:4326) and are available as GeoJSON or GeoParquet, with JSON
lookup tables where they are handy.

## Layers

These links point to the latest files on S3. The inventory was rebuilt on
{generated_date}; “source updated” is the latest date reported by the upstream
publisher when one is available.

| Layer | Description | Features | Geometry | Source updated | Source |
| --- | --- | ---: | --- | --- | --- |
{inventory}

Want to work with the inventory programmatically? Start with
[`catalog.json`]({PUBLIC_BASE_URL}/catalog.json).{derived_section}

## Census demographics

The Census does not publish ready-made totals for Palm Springs neighborhood
organizations or city voting precincts, so this project estimates them from 2020
Decennial Census PL 94-171 blocks. The output includes total population, race and
ethnicity, voting-age population and occupied and vacant housing units.

When a block crosses a local boundary, the pipeline first divides its counts
according to the addresses on each side. If the block has no addresses, it falls
back to building-footprint area and then land area. The full block stays in the
denominator throughout, which means people outside the target boundaries remain
unassigned instead of being pushed into the nearest neighborhood or precinct.

## Daily climate normals

The daily collector gets the maximum temperature and departure from normal for
[SERCC Climate Perspectives](https://sercc.oasis.unc.edu/about.php) station
`048892`, Thermal Fcwos. It calculates the reported normal by subtracting the
departure from the observed maximum. The station is labeled as Palm Springs by
the source, though its coordinates are near Thermal.

Download the history as
[JSON](https://stilesdata.com/palm-springs/climate/daily-max-temperature.json) or
[CSV](https://stilesdata.com/palm-springs/climate/daily-max-temperature.csv).
Each run revisits the latest seven days so late reports and source corrections
replace earlier values.

## Update the data

To rebuild and upload the collection yourself, use Python 3.11 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make update
make update-climate
```

To add a city layer, edit [`sources.json`](sources.json). Derived sources live in
[`derived-sources.json`](derived-sources.json), while Census variables and
targets live in [`census.json`](census.json). Climate collection settings live
in [`climate.json`](climate.json). Layer IDs need to be unique, lowercase and
kebab-cased.

The build is all-or-nothing: if any download or derivation fails, the published
files are left untouched.

Updates upload to `s3://stilesdata.com/palm-springs/data/`. If
`AWS_PROFILE_NAME` is set, the uploader uses that AWS profile; otherwise it uses
the default AWS credential chain. Override `BUCKET` or `PREFIX` when needed.

The [weekly workflow](.github/workflows/update-data.yml) runs every Monday,
uploads the current files to S3 and refreshes this README. It needs
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` repository secrets, and it can
also be started manually from the Actions tab.

The [daily climate workflow](.github/workflows/update-climate.yml) uses the same
AWS secrets and publishes its files under
`s3://stilesdata.com/palm-springs/climate/`.

Weekly runs reuse the published 2020 Census block cache. Set `CENSUS_REFRESH=1`
and provide `CENSUS_API_KEY` to rebuild that static cache from official sources.

## Source and reuse

The City of Palm Springs remains the authoritative source for its municipal
layers. This project republishes those files for convenience and clearly labels
the extra sources, methods and licenses used for derived layers. Follow the
source links above before relying on a file for official or legal purposes.
"""


def publish(staging_dir: Path, catalog: dict[str, Any]) -> None:
    """Replace generated files only after every layer succeeds."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    expected_files = {"catalog.json"}
    for layer in catalog["layers"]:
        artifacts = layer.get("artifacts")
        if artifacts:
            for artifact in artifacts:
                filename = artifact["filename"]
                expected_files.add(filename)
                shutil.move(staging_dir / filename, DATA_DIR / filename)
        else:
            filename = f"{layer['id']}.geojson"
            expected_files.add(filename)
            shutil.move(staging_dir / filename, DATA_DIR / filename)
        license_data_url = layer.get("license_data_url")
        if license_data_url:
            license_filename = license_data_url.rsplit("/", 1)[-1]
            expected_files.add(license_filename)
            shutil.move(
                staging_dir / license_filename,
                DATA_DIR / license_filename,
            )

    for existing_path in DATA_DIR.iterdir():
        if existing_path.name not in expected_files:
            existing_path.unlink()

    CATALOG_PATH.write_text(json.dumps(catalog, indent=2) + "\n")
    README_PATH.write_text(build_readme(catalog))


def main() -> None:
    """Download all configured layers and publish the complete snapshot."""
    sources = load_sources()
    derived_sources = load_derived_sources()
    census_config = load_census_config(CENSUS_CONFIG_PATH)
    duplicate_ids = {source["layer"] for source in sources} & {
        source["layer"] for source in derived_sources
    }
    if duplicate_ids:
        raise ValueError(
            f"Layer IDs appear in both source files: {', '.join(sorted(duplicate_ids))}"
        )
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()

    with tempfile.TemporaryDirectory(prefix="palm-springs-") as temp_dir:
        staging_dir = Path(temp_dir)
        layers = [
            download_layer(source, staging_dir, generated_at) for source in sources
        ]
        layers.extend(
            derive_layer(source, staging_dir, generated_at)
            for source in derived_sources
        )
        layers.extend(
            build_census_outputs(
                census_config,
                staging_dir,
                generated_at,
                PUBLIC_BASE_URL,
            )
        )
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
