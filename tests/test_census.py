import json

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, box

from census import (
    HOUSING_LEAVES,
    POPULATION_LEAVES,
    ROUNDING_LEAVES,
    VAP_LEAVES,
    apportion_blocks,
    calculate_hybrid_weights,
    largest_remainder,
    prepare_targets,
    validate_demographic_output,
    write_demographic_json,
)


def support_frames():
    blocks = gpd.GeoDataFrame(
        {
            "block_geoid": ["b-address", "b-building", "b-land"],
            "geometry": [
                box(0, 0, 10, 10),
                box(10, 0, 20, 10),
                box(20, 0, 30, 10),
            ],
        },
        crs="EPSG:3310",
    )
    targets = gpd.GeoDataFrame(
        {
            "target_id": ["address", "building", "land"],
            "geometry": [
                box(0, 0, 5, 10),
                box(10, 0, 15, 10),
                box(20, 0, 25, 10),
            ],
        },
        crs="EPSG:3310",
    )
    addresses = gpd.GeoDataFrame(
        {"geometry": [Point(2, 2), Point(8, 2)]}, crs="EPSG:3310"
    )
    buildings = gpd.GeoDataFrame(
        {
            "geometry": [
                box(11, 1, 13, 3),
                box(16, 1, 20, 3),
            ]
        },
        crs="EPSG:3310",
    )
    return blocks, targets, addresses, buildings


def demographic_blocks():
    blocks, _, _, _ = support_frames()
    for column in ROUNDING_LEAVES:
        blocks[column] = 2
    blocks["pop_not_hispanic"] = blocks[POPULATION_LEAVES[1:]].sum(axis=1)
    blocks["pop_total"] = blocks[POPULATION_LEAVES].sum(axis=1)
    blocks["vap_not_hispanic"] = blocks[VAP_LEAVES[1:]].sum(axis=1)
    blocks["vap_total"] = blocks[VAP_LEAVES].sum(axis=1)
    blocks["housing_total"] = blocks[HOUSING_LEAVES].sum(axis=1)
    return blocks


def test_hybrid_weights_use_each_fallback_and_preserve_remainder():
    blocks, targets, addresses, buildings = support_frames()

    weights = calculate_hybrid_weights(
        blocks, targets, addresses, buildings, "target_id", "EPSG:3310"
    ).set_index("target_id")

    assert weights.loc["address", "allocation_method"] == "address"
    assert weights.loc["address", "weight"] == pytest.approx(0.5)
    assert weights.loc["building", "allocation_method"] == "building"
    assert weights.loc["building", "weight"] == pytest.approx(1 / 3)
    assert weights.loc["land", "allocation_method"] == "land"
    assert weights.loc["land", "weight"] == pytest.approx(0.5)


def test_overlap_weights_never_exceed_one():
    blocks, _, addresses, buildings = support_frames()
    overlapping = gpd.GeoDataFrame(
        {
            "target_id": ["a", "b"],
            "geometry": [box(0, 0, 7, 10), box(3, 0, 10, 10)],
        },
        crs="EPSG:3310",
    )

    weights = calculate_hybrid_weights(
        blocks.iloc[[0]], overlapping, addresses, buildings, "target_id", "EPSG:3310"
    )

    assert weights["weight"].sum() <= 1


def test_largest_remainder_is_conservative_and_deterministic():
    rounded = largest_remainder(pd.Series({"a": 1.4, "b": 1.4, "sink": 0.2}), 3)

    assert rounded.to_dict() == {"a": 2, "b": 1, "sink": 0}
    assert rounded.sum() == 3


def test_apportionment_reports_unassigned_counts_and_category_identities():
    blocks = demographic_blocks()
    _, targets, addresses, buildings = support_frames()

    enriched, qa = apportion_blocks(
        blocks, targets, addresses, buildings, "target_id", "EPSG:3310"
    )

    assert qa["unassigned_totals"]["pop_total"] > 0
    assert (
        enriched["pop_hispanic"] + enriched["pop_not_hispanic"]
        == enriched["pop_total"]
    ).all()
    assert (
        enriched["housing_occupied"] + enriched["housing_vacant"]
        == enriched["housing_total"]
    ).all()
    assert (
        int(enriched["pop_total"].sum()) + qa["unassigned_totals"]["pop_total"]
        == int(blocks["pop_total"].sum())
    )


def test_validation_rejects_negative_counts():
    blocks = demographic_blocks()
    _, targets, addresses, buildings = support_frames()
    enriched, qa = apportion_blocks(
        blocks, targets, addresses, buildings, "target_id", "EPSG:3310"
    )
    assigned = int(enriched["pop_total"].sum())
    qa.update(
        {
            "official_place_population": assigned,
            "assigned_population": assigned,
            "place_population_difference": 0,
            "place_population_difference_pct": 0,
        }
    )
    validate_demographic_output(enriched, qa)

    invalid = enriched.copy()
    invalid.loc[0, "pop_hispanic"] = -1
    with pytest.raises(RuntimeError, match="negative"):
        validate_demographic_output(invalid, qa)


def test_prepare_targets_clips_to_city_boundary():
    targets = gpd.GeoDataFrame(
        {"target_id": ["one"], "geometry": [box(0, 0, 2, 1)]},
        crs="EPSG:3310",
    )
    city = gpd.GeoDataFrame(
        {"geometry": [box(0, 0, 1, 1)]}, crs="EPSG:3310"
    )

    clipped = prepare_targets(targets, "target_id", city, "EPSG:3310")

    assert clipped.geometry.iloc[0].area == pytest.approx(1)


def test_demographic_json_is_keyed_by_target_id(tmp_path):
    enriched = gpd.GeoDataFrame(
        {
            "target_id": ["b", "a"],
            "pop_total": [20, 10],
            "geometry": [box(1, 0, 2, 1), box(0, 0, 1, 1)],
        },
        crs="EPSG:4326",
    )
    output = tmp_path / "demographics.json"

    write_demographic_json(
        output,
        enriched,
        "target_id",
        ["pop_total"],
        {"census_vintage": 2020},
    )

    payload = json.loads(output.read_text())
    assert list(payload["data"]) == ["a", "b"]
    assert payload["data"]["a"]["pop_total"] == 10
