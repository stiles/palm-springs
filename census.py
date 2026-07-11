"""Fetch and apportion 2020 Census blocks to Palm Springs geographies."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pygris
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

POPULATION_LEAVES = [
    "pop_hispanic",
    "pop_white_nh",
    "pop_black_nh",
    "pop_aian_nh",
    "pop_asian_nh",
    "pop_nhpi_nh",
    "pop_other_nh",
    "pop_two_or_more_nh",
]
VAP_LEAVES = [
    "vap_hispanic",
    "vap_white_nh",
    "vap_black_nh",
    "vap_aian_nh",
    "vap_asian_nh",
    "vap_nhpi_nh",
    "vap_other_nh",
    "vap_two_or_more_nh",
]
HOUSING_LEAVES = ["housing_occupied", "housing_vacant"]
ROUNDING_LEAVES = POPULATION_LEAVES + VAP_LEAVES + HOUSING_LEAVES


def request_session() -> requests.Session:
    """Create a retrying HTTP session."""
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def load_census_config(path: Path) -> dict[str, Any]:
    """Load and minimally validate Census configuration."""
    config = json.loads(path.read_text())
    required = {
        "year",
        "dataset",
        "state_fips",
        "county_fips",
        "place_fips",
        "place_name",
        "official_place_population",
        "projected_crs",
        "cache_filename",
        "source",
        "source_url",
        "api_url",
        "variables",
        "targets",
    }
    missing = required - config.keys()
    if missing:
        raise ValueError(f"Census config is missing: {', '.join(sorted(missing))}")
    if not config["variables"] or not config["targets"]:
        raise ValueError("Census variables and targets must be nonempty")
    return config


def fetch_census_counts(
    config: dict[str, Any], session: requests.Session | None = None
) -> pd.DataFrame:
    """Fetch block-level PL 94-171 counts from the Census API."""
    session = session or request_session()
    params: list[tuple[str, str]] = [
        ("get", ",".join(config["variables"])),
        ("for", "block:*"),
        ("in", f"state:{config['state_fips']}"),
        ("in", f"county:{config['county_fips']}"),
        ("in", "tract:*"),
    ]
    api_key = os.getenv("CENSUS_API_KEY")
    if api_key:
        params.append(("key", api_key))

    response = session.get(config["api_url"], params=params, timeout=180)
    response.raise_for_status()
    if "missing_key" in response.url:
        raise RuntimeError(
            "CENSUS_API_KEY is required when rebuilding the Census block cache"
        )
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError(f"Unexpected Census API response: {payload}")

    frame = pd.DataFrame(payload[1:], columns=payload[0])
    frame["block_geoid"] = (
        frame["state"] + frame["county"] + frame["tract"] + frame["block"]
    )
    for source_field, output_field in config["variables"].items():
        frame[output_field] = pd.to_numeric(frame[source_field], errors="raise")
    return frame[["block_geoid", *config["variables"].values()]]


def fetch_block_geometries(
    config: dict[str, Any], boundary: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Fetch 2020 TIGER block polygons for Riverside County."""
    blocks = pygris.blocks(
        state=config["state_fips"],
        county=config["county_fips"],
        year=config["year"],
        cache=True,
        subset_by=boundary,
    ).to_crs("EPSG:4326")
    columns = {column.lower(): column for column in blocks.columns}
    geoid_column = next(
        (
            columns[candidate]
            for candidate in ("geoid20", "geoid", "geoid10")
            if candidate in columns
        ),
        None,
    )
    if geoid_column is None:
        raise RuntimeError(f"TIGER blocks have no GEOID field: {list(blocks.columns)}")
    return blocks[[geoid_column, "geometry"]].rename(
        columns={geoid_column: "block_geoid"}
    )


def fetch_enriched_blocks(
    config: dict[str, Any], boundary: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Join official block geometries and PL counts and limit to the city."""
    geometries = fetch_block_geometries(config, boundary)
    counts = fetch_census_counts(config)
    blocks = geometries.merge(counts, on="block_geoid", how="left", validate="1:1")
    value_columns = list(config["variables"].values())
    if blocks[value_columns].isna().any().any():
        missing = int(blocks[value_columns].isna().any(axis=1).sum())
        raise RuntimeError(f"{missing} TIGER blocks lack Census counts")

    city = boundary.to_crs("EPSG:4326").geometry.union_all()
    blocks = blocks[blocks.geometry.intersects(city)].copy()
    blocks = blocks.sort_values("block_geoid").reset_index(drop=True)
    if blocks.empty:
        raise RuntimeError("No Census blocks intersect the Palm Springs boundary")
    return blocks


def load_or_fetch_blocks(
    config: dict[str, Any],
    boundary: gpd.GeoDataFrame,
    staging_dir: Path,
    public_base_url: str,
) -> tuple[gpd.GeoDataFrame, str]:
    """Reuse the public static block cache unless an explicit refresh is requested."""
    cache_path = staging_dir / config["cache_filename"]
    cache_url = f"{public_base_url}/{config['cache_filename']}"
    refresh = os.getenv("CENSUS_REFRESH", "").lower() in {"1", "true", "yes"}

    if not refresh:
        response = request_session().get(cache_url, timeout=120)
        if response.status_code == 200:
            cache_path.write_bytes(response.content)
            blocks = gpd.read_parquet(cache_path)
            return blocks, "cache"
        if response.status_code not in {403, 404}:
            response.raise_for_status()

    blocks = fetch_enriched_blocks(config, boundary)
    blocks.to_parquet(cache_path, index=False)
    return blocks, "census"


def prepare_targets(
    targets: gpd.GeoDataFrame,
    id_field: str,
    boundary: gpd.GeoDataFrame,
    projected_crs: str,
) -> gpd.GeoDataFrame:
    """Validate, clip and project target polygons."""
    if id_field not in targets.columns:
        raise RuntimeError(f"Target layer is missing ID field {id_field}")
    if targets[id_field].isna().any() or targets[id_field].duplicated().any():
        raise RuntimeError(f"Target IDs in {id_field} must be unique and non-null")

    projected = targets.to_crs(projected_crs)
    city = boundary.to_crs(projected_crs).geometry.union_all()
    projected["geometry"] = projected.geometry.intersection(city)
    projected = projected[~projected.geometry.is_empty].copy()
    projected[id_field] = projected[id_field].astype(str)
    return projected.sort_values(id_field).reset_index(drop=True)


def _point_support(
    addresses: gpd.GeoDataFrame,
    blocks: gpd.GeoDataFrame,
    intersections: gpd.GeoDataFrame,
    id_field: str,
) -> tuple[pd.Series, pd.Series]:
    """Count address points in full blocks and block-target intersections."""
    points = addresses[["geometry"]].copy()
    points["_support_id"] = np.arange(len(points))
    block_hits = gpd.sjoin(
        points,
        blocks[["block_geoid", "geometry"]],
        how="inner",
        predicate="within",
    )
    block_totals = block_hits.groupby("block_geoid")["_support_id"].nunique()

    target_hits = gpd.sjoin(
        points,
        intersections[["block_geoid", id_field, "geometry"]],
        how="inner",
        predicate="within",
    ).reset_index(drop=True)
    target_hits = target_hits.sort_values(
        ["_support_id", "block_geoid", id_field]
    ).drop_duplicates(["_support_id", "block_geoid"])
    target_totals = target_hits.groupby(["block_geoid", id_field])[
        "_support_id"
    ].nunique()
    return block_totals, target_totals


def _building_support(
    buildings: gpd.GeoDataFrame,
    zero_address_blocks: gpd.GeoDataFrame,
    targets: gpd.GeoDataFrame,
    id_field: str,
) -> tuple[pd.Series, pd.Series]:
    """Measure building area support for blocks without address points."""
    if zero_address_blocks.empty:
        empty = pd.Series(dtype="float64")
        return empty, empty

    fragments = gpd.overlay(
        buildings[["geometry"]],
        zero_address_blocks[["block_geoid", "geometry"]],
        how="intersection",
        keep_geom_type=True,
    )
    if fragments.empty:
        empty = pd.Series(dtype="float64")
        return empty, empty
    fragments["_building_area"] = fragments.geometry.area
    block_totals = fragments.groupby("block_geoid")["_building_area"].sum()

    target_fragments = gpd.overlay(
        fragments[["block_geoid", "geometry"]],
        targets[[id_field, "geometry"]],
        how="intersection",
        keep_geom_type=True,
    )
    target_fragments["_building_area"] = target_fragments.geometry.area
    target_totals = target_fragments.groupby(["block_geoid", id_field])[
        "_building_area"
    ].sum()
    return block_totals, target_totals


def calculate_hybrid_weights(
    blocks: gpd.GeoDataFrame,
    targets: gpd.GeoDataFrame,
    addresses: gpd.GeoDataFrame,
    buildings: gpd.GeoDataFrame,
    id_field: str,
    projected_crs: str,
) -> gpd.GeoDataFrame:
    """Calculate address, building and land fallback weights by block-target pair."""
    blocks_proj = blocks.to_crs(projected_crs)
    targets_proj = targets.to_crs(projected_crs)
    addresses_proj = addresses.to_crs(projected_crs)
    buildings_proj = buildings.to_crs(projected_crs)

    intersections = gpd.overlay(
        blocks_proj[["block_geoid", "geometry"]],
        targets_proj[[id_field, "geometry"]],
        how="intersection",
        keep_geom_type=True,
    )
    intersections["_land_area"] = intersections.geometry.area
    intersections = (
        intersections.groupby(["block_geoid", id_field], as_index=False)
        .agg({"_land_area": "sum", "geometry": lambda values: values.union_all()})
        .set_geometry("geometry", crs=projected_crs)
    )

    address_blocks, address_targets = _point_support(
        addresses_proj, blocks_proj, intersections, id_field
    )
    block_ids = blocks_proj["block_geoid"]
    zero_address_ids = block_ids[
        block_ids.map(address_blocks).fillna(0).eq(0)
    ]
    zero_address_blocks = blocks_proj[
        blocks_proj["block_geoid"].isin(zero_address_ids)
    ]
    building_blocks, building_targets = _building_support(
        buildings_proj, zero_address_blocks, targets_proj, id_field
    )
    block_areas = blocks_proj.set_index("block_geoid").geometry.area

    methods: list[str] = []
    weights: list[float] = []
    for _, row in intersections.iterrows():
        block_geoid = row["block_geoid"]
        key = (block_geoid, row[id_field])
        address_total = float(address_blocks.get(block_geoid, 0))
        building_total = float(building_blocks.get(block_geoid, 0))
        if address_total > 0:
            methods.append("address")
            weights.append(float(address_targets.get(key, 0)) / address_total)
        elif building_total > 0:
            methods.append("building")
            weights.append(float(building_targets.get(key, 0)) / building_total)
        else:
            methods.append("land")
            weights.append(row["_land_area"] / float(block_areas[block_geoid]))

    intersections["allocation_method"] = methods
    intersections["weight"] = weights
    weight_sums = intersections.groupby("block_geoid")["weight"].transform("sum")
    overlap = weight_sums > 1
    intersections.loc[overlap, "weight"] = (
        intersections.loc[overlap, "weight"] / weight_sums[overlap]
    )
    return intersections


def largest_remainder(values: pd.Series, total: int) -> pd.Series:
    """Round nonnegative allocations to integers that sum to the source total."""
    values = values.clip(lower=0)
    floors = np.floor(values).astype("int64")
    remainder = int(total - floors.sum())
    if remainder < 0:
        raise RuntimeError("Rounded allocations exceed the source total")
    if remainder:
        fractions = values - floors
        order = sorted(
            range(len(values)),
            key=lambda index: (-fractions.iloc[index], str(values.index[index])),
        )
        for index in order[:remainder]:
            floors.iloc[index] += 1
    return floors


def _derive_parent_counts(frame: pd.DataFrame) -> None:
    """Derive category parents from rounded leaves to preserve identities."""
    frame["pop_not_hispanic"] = frame[POPULATION_LEAVES[1:]].sum(axis=1)
    frame["pop_total"] = frame[POPULATION_LEAVES].sum(axis=1)
    frame["vap_not_hispanic"] = frame[VAP_LEAVES[1:]].sum(axis=1)
    frame["vap_total"] = frame[VAP_LEAVES].sum(axis=1)
    frame["housing_total"] = frame[HOUSING_LEAVES].sum(axis=1)


def apportion_blocks(
    blocks: gpd.GeoDataFrame,
    targets: gpd.GeoDataFrame,
    addresses: gpd.GeoDataFrame,
    buildings: gpd.GeoDataFrame,
    id_field: str,
    projected_crs: str,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Apportion block counts and return enriched targets plus QA metadata."""
    weights = calculate_hybrid_weights(
        blocks, targets, addresses, buildings, id_field, projected_crs
    )
    value_columns = [
        column
        for column in blocks.columns
        if column not in {"block_geoid", "geometry"}
    ]
    weighted = weights.merge(
        blocks[["block_geoid", *value_columns]], on="block_geoid", validate="m:1"
    )
    for column in value_columns:
        weighted[column] = weighted[column] * weighted["weight"]

    raw = weighted.groupby(id_field)[value_columns].sum()
    source_totals = blocks[value_columns].sum().astype("int64")
    rounded = pd.DataFrame(index=raw.index)
    unassigned: dict[str, int] = {}
    for column in ROUNDING_LEAVES:
        sink = max(float(source_totals[column]) - float(raw[column].sum()), 0.0)
        allocations = pd.concat(
            [raw[column], pd.Series({"__unassigned__": sink})]
        )
        integers = largest_remainder(allocations, int(source_totals[column]))
        rounded[column] = integers.drop("__unassigned__")
        unassigned[column] = int(integers["__unassigned__"])
    _derive_parent_counts(rounded)

    unassigned_frame = pd.DataFrame([unassigned])
    _derive_parent_counts(unassigned_frame)
    unassigned = {
        column: int(unassigned_frame.iloc[0][column])
        for column in rounded.columns
    }

    block_counts = weighted[weighted["weight"] > 0].groupby(id_field)[
        "block_geoid"
    ].nunique()
    method_population = (
        weighted.assign(_method_pop=weighted["pop_total"])
        .groupby([id_field, "allocation_method"])["_method_pop"]
        .sum()
        .unstack(fill_value=0)
    )
    method_denominator = method_population.sum(axis=1).replace(0, np.nan)
    for method in ("address", "building", "land"):
        rounded[f"allocation_{method}_share"] = (
            method_population.get(method, 0) / method_denominator
        ).fillna(0)
    rounded["source_blocks_count"] = block_counts.reindex(rounded.index).fillna(0)

    enriched = targets.merge(
        rounded.reset_index(), on=id_field, how="left", validate="1:1"
    )
    numeric_columns = list(rounded.columns)
    enriched[numeric_columns] = enriched[numeric_columns].fillna(0)
    qa = {
        "source_blocks_count": int(len(blocks)),
        "source_totals": {
            column: int(source_totals[column]) for column in rounded.columns
            if column in source_totals
        },
        "unassigned_totals": unassigned,
        "allocation_methods": {
            method: int(
                weights.loc[weights["allocation_method"] == method, "block_geoid"]
                .nunique()
            )
            for method in ("address", "building", "land")
        },
    }
    return enriched, qa


def _json_scalar(value: Any) -> Any:
    """Convert dataframe scalars to strict JSON values."""
    if value is None or pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def validate_demographic_output(
    enriched: gpd.GeoDataFrame, qa: dict[str, Any]
) -> None:
    """Fail loudly when apportioned counts violate core Census invariants."""
    count_fields = [
        *POPULATION_LEAVES,
        "pop_not_hispanic",
        "pop_total",
        *VAP_LEAVES,
        "vap_not_hispanic",
        "vap_total",
        *HOUSING_LEAVES,
        "housing_total",
    ]
    if enriched[count_fields].isna().any().any():
        raise RuntimeError("Apportioned demographics contain null counts")
    if (enriched[count_fields] < 0).any().any():
        raise RuntimeError("Apportioned demographics contain negative counts")
    if not (
        enriched["pop_hispanic"] + enriched["pop_not_hispanic"]
        == enriched["pop_total"]
    ).all():
        raise RuntimeError("Population categories do not sum to total population")
    if not (
        enriched["vap_hispanic"] + enriched["vap_not_hispanic"]
        == enriched["vap_total"]
    ).all():
        raise RuntimeError("Voting-age categories do not sum to total VAP")
    if not (
        enriched["housing_occupied"] + enriched["housing_vacant"]
        == enriched["housing_total"]
    ).all():
        raise RuntimeError("Housing categories do not sum to total housing")

    for field in count_fields:
        assigned = int(enriched[field].sum())
        unassigned = int(qa["unassigned_totals"][field])
        source = int(qa["source_totals"][field])
        if assigned + unassigned != source:
            raise RuntimeError(
                f"{field} is not conserved: {assigned} + {unassigned} != {source}"
            )
    if abs(float(qa["place_population_difference_pct"])) > 1:
        raise RuntimeError(
            "Apportioned population differs from the official place count by "
            f"{qa['place_population_difference_pct']:.2f}%"
        )


def write_demographic_json(
    path: Path,
    enriched: gpd.GeoDataFrame,
    id_field: str,
    demographic_fields: list[str],
    metadata: dict[str, Any],
) -> None:
    """Write a deterministic ID-keyed demographic lookup table."""
    data: dict[str, dict[str, Any]] = {}
    for _, row in enriched.sort_values(id_field).iterrows():
        data[str(row[id_field])] = {
            field: _json_scalar(row[field]) for field in demographic_fields
        }
    payload = {"metadata": metadata, "data": data}
    path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")


def build_census_outputs(
    config: dict[str, Any],
    staging_dir: Path,
    generated_at: str,
    public_base_url: str,
) -> list[dict[str, Any]]:
    """Build the block cache and target demographic sidecars."""
    print("Loading 2020 Census blocks...")
    boundary = gpd.read_file(staging_dir / "city-boundary.geojson")
    addresses = gpd.read_file(staging_dir / "addresses.geojson")
    buildings = gpd.read_file(
        staging_dir / "building-footprints.geojson",
        columns=["building_id"],
    )
    blocks, cache_source = load_or_fetch_blocks(
        config, boundary, staging_dir, public_base_url
    )
    cache_url = f"{public_base_url}/{config['cache_filename']}"
    census_date = f"{config['year']}-04-01"
    entries: list[dict[str, Any]] = [
        {
            "id": f"census-blocks-{config['year']}",
            "title": f"{config['year']} Census blocks",
            "description": (
                "Tabulation blocks intersecting Palm Springs with PL 94-171 "
                "population, voting-age population, race, ethnicity and housing counts."
            ),
            "feature_count": len(blocks),
            "geometry_type": "Polygon",
            "crs": "EPSG:4326",
            "layer_type": "source",
            "source": config["source"],
            "source_url": config["source_url"],
            "source_link_label": "Census",
            "data_url": cache_url,
            "source_last_updated": census_date,
            "downloaded_at": generated_at,
            "cache_source": cache_source,
            "artifacts": [
                {
                    "filename": config["cache_filename"],
                    "format": "GeoParquet",
                    "url": cache_url,
                }
            ],
        }
    ]

    demographic_fields = [
        *POPULATION_LEAVES,
        "pop_not_hispanic",
        "pop_total",
        *VAP_LEAVES,
        "vap_not_hispanic",
        "vap_total",
        *HOUSING_LEAVES,
        "housing_total",
        "source_blocks_count",
        "allocation_address_share",
        "allocation_building_share",
        "allocation_land_share",
    ]
    for target_config in config["targets"]:
        source_layer = target_config["layer"]
        id_field = target_config["id_field"]
        output_id = f"{source_layer}-demographics"
        targets = gpd.read_file(staging_dir / f"{source_layer}.geojson")
        targets = prepare_targets(
            targets, id_field, boundary, config["projected_crs"]
        )
        enriched, qa = apportion_blocks(
            blocks,
            targets,
            addresses,
            buildings,
            id_field,
            config["projected_crs"],
        )
        assigned_population = int(enriched["pop_total"].sum())
        official_population = int(config["official_place_population"])
        qa["official_place_population"] = official_population
        qa["assigned_population"] = assigned_population
        qa["place_population_difference"] = assigned_population - official_population
        qa["place_population_difference_pct"] = (
            (assigned_population - official_population) / official_population * 100
        )
        validate_demographic_output(enriched, qa)
        enriched["census_vintage"] = config["year"]
        enriched["apportioned_at"] = generated_at
        enriched["source_layer"] = source_layer
        enriched = enriched.to_crs("EPSG:4326").sort_values(id_field)

        parquet_filename = f"{output_id}.parquet"
        json_filename = f"{output_id}.json"
        enriched.to_parquet(staging_dir / parquet_filename, index=False)
        metadata = {
            "source": config["source"],
            "source_url": config["source_url"],
            "census_vintage": config["year"],
            "target_layer": source_layer,
            "target_id_field": id_field,
            "method": (
                "address share; building-area fallback; land-area fallback; "
                "full blocks retained as denominator"
            ),
            "generated_at": generated_at,
            **qa,
        }
        write_demographic_json(
            staging_dir / json_filename,
            enriched,
            id_field,
            demographic_fields,
            metadata,
        )
        parquet_url = f"{public_base_url}/{parquet_filename}"
        json_url = f"{public_base_url}/{json_filename}"
        entries.append(
            {
                "id": output_id,
                "title": target_config["title"],
                "description": target_config["description"],
                "feature_count": len(enriched),
                "geometry_type": "Polygon",
                "crs": "EPSG:4326",
                "layer_type": "derived",
                "source": config["source"],
                "source_url": config["source_url"],
                "source_link_label": "Census",
                "data_url": parquet_url,
                "source_last_updated": census_date,
                "downloaded_at": generated_at,
                "derived_from": [
                    source_layer,
                    f"census-blocks-{config['year']}",
                    "addresses",
                    "building-footprints",
                ],
                "method": metadata["method"],
                "census_vintage": config["year"],
                "target_id_field": id_field,
                "qa": qa,
                "artifacts": [
                    {
                        "filename": parquet_filename,
                        "format": "GeoParquet",
                        "url": parquet_url,
                    },
                    {
                        "filename": json_filename,
                        "format": "JSON",
                        "url": json_url,
                    },
                ],
            }
        )
    return entries
