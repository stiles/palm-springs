import json
import zipfile

import geopandas as gpd
import mercantile
from shapely.geometry import Point, Polygon, box

from download import (
    build_readme,
    derive_static_vector,
    enrich_buildings,
    polygonal_geometry,
    select_manifest_tiles,
    stable_building_id,
    write_feature_collection,
)


def address_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "AddressID": "a-2",
                "Address": "2 TEST ST",
                "Parcel_APN": "002",
                "Unit": None,
                "ZipCode": "92262",
                "Neighborhood": "Test",
                "geometry": Point(1.5, 1),
            },
            {
                "AddressID": "a-1",
                "Address": "1 TEST ST",
                "Parcel_APN": "001",
                "Unit": "A",
                "ZipCode": "92262",
                "Neighborhood": "Test",
                "geometry": Point(0.5, 1),
            },
            {
                "AddressID": "boundary",
                "Address": "3 TEST ST",
                "Parcel_APN": "003",
                "Unit": None,
                "ZipCode": "92262",
                "Neighborhood": "Test",
                "geometry": Point(0, 1),
            },
        ],
        crs="EPSG:4326",
    )


def building_frame() -> gpd.GeoDataFrame:
    first = box(0, 0, 2, 2)
    second = box(3, 0, 4, 1)
    return gpd.GeoDataFrame(
        [
            {
                "building_id": stable_building_id(first),
                "height": 4.5,
                "confidence": None,
                "geometry": first,
            },
            {
                "building_id": stable_building_id(second),
                "height": None,
                "confidence": 0.9,
                "geometry": second,
            },
        ],
        crs="EPSG:4326",
    )


def derived_catalog() -> dict:
    return {
        "generated_at": "2026-07-10T12:00:00+00:00",
        "crs": "EPSG:4326",
        "layers": [
            {
                "id": "building-footprints",
                "title": "Building footprints",
                "description": "Derived buildings.",
                "feature_count": 2,
                "geometry_type": "Polygon",
                "crs": "EPSG:4326",
                "layer_type": "derived",
                "source": "Microsoft GlobalML Building Footprints",
                "source_url": "https://example.com/source",
                "source_link_label": "Microsoft",
                "data_url": "https://example.com/buildings.geojson",
                "source_last_updated": "2026-02-23",
                "downloaded_at": "2026-07-10T12:00:00+00:00",
                "derived_from": ["city-boundary", "addresses"],
                "method": "clip and contain",
                "license": "CDLA Permissive 2.0",
                "license_url": "https://example.com/license",
                "license_data_url": "https://example.com/license.txt",
                "source_tiles": [],
            }
        ],
    }


def test_select_manifest_tiles_for_bounds():
    tile = mercantile.tile(-116.5, 33.8, 9)
    quadkey = mercantile.quadkey(tile)
    tile_bounds = mercantile.bounds(tile)
    bounds = (
        tile_bounds.west + 0.01,
        tile_bounds.south + 0.01,
        tile_bounds.east - 0.01,
        tile_bounds.north - 0.01,
    )
    manifest = "\n".join(
        [
            "Location,QuadKey,Url,Size,UploadDate",
            f"UnitedStates,{quadkey},https://example.com/a.gz,1KB,2026-01-01",
            f"Canada,{quadkey},https://example.com/b.gz,1KB,2026-01-01",
        ]
    )

    selected = select_manifest_tiles(manifest, bounds, "UnitedStates", 9)

    assert [row["QuadKey"] for row in selected] == [quadkey]


def test_polygon_clipping_and_stable_id():
    clipped = polygonal_geometry(box(-1, -1, 1, 1).intersection(box(0, 0, 2, 2)))
    reversed_ring = Polygon(list(clipped.exterior.coords)[::-1])

    assert clipped.area == 1
    assert stable_building_id(clipped) == stable_building_id(reversed_ring)


def test_enrich_buildings_uses_strict_containment():
    features = enrich_buildings(building_frame(), address_frame())
    by_id = {feature["properties"]["building_id"]: feature for feature in features}
    first_id = stable_building_id(box(0, 0, 2, 2))
    second_id = stable_building_id(box(3, 0, 4, 1))

    assert by_id[first_id]["properties"]["address_count"] == 2
    assert [
        address["AddressID"] for address in by_id[first_id]["properties"]["addresses"]
    ] == ["a-1", "a-2"]
    assert by_id[second_id]["properties"]["address_count"] == 0
    assert by_id[second_id]["properties"]["addresses"] == []


def test_write_feature_collection_preserves_nested_addresses(tmp_path):
    output = tmp_path / "buildings.geojson"
    features = enrich_buildings(building_frame(), address_frame())

    write_feature_collection(output, features)
    payload = json.loads(output.read_text())

    assert payload["type"] == "FeatureCollection"
    assert isinstance(payload["features"][0]["properties"]["addresses"], list)


def test_derive_static_vector_reprojects_archived_shapefile(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source_path = source_dir / "footprints.shp"
    gpd.GeoDataFrame(
        [{"source_id": "1", "geometry": box(6_450_000, 2_200_000, 6_450_100, 2_200_100)}],
        crs="EPSG:2230",
    ).to_file(source_path)
    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in source_dir.iterdir():
            archive.write(path, f"source/{path.name}")

    class Response:
        content = archive_path.read_bytes()

        def raise_for_status(self):
            return None

    class Session:
        def get(self, url, timeout):
            return Response()

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    metadata = derive_static_vector(
        {
            "layer": "city-buildings",
            "title": "City buildings",
            "description": "City records.",
            "archive_url": "https://example.com/source.zip",
            "archive_layer": "source/footprints.shp",
            "source": "City",
            "source_url": "https://example.com/source.zip",
            "source_last_updated": "2026-07-29",
            "source_crs": "EPSG:2230",
            "expected_feature_count": 1,
            "contact": "gis@example.com",
            "disclaimer": "As is.",
            "inputs": [],
        },
        staging_dir,
        "2026-07-30T00:00:00+00:00",
        session=Session(),
    )

    output = gpd.read_parquet(staging_dir / "city-buildings.parquet")
    assert output.crs.to_epsg() == 4326
    assert metadata["feature_count"] == 1
    assert [artifact["format"] for artifact in metadata["artifacts"]] == [
        "GeoJSON",
        "GeoParquet",
    ]


def test_readme_documents_derived_source_and_license():
    readme = build_readme(derived_catalog())

    assert "[Microsoft](https://example.com/source)" in readme
    assert "## Derived layers" in readme
    assert "[CDLA Permissive 2.0](https://example.com/license)" in readme


def test_readme_documents_city_cpra_release():
    catalog = derived_catalog()
    catalog["layers"].append(
        {
            "id": "city-building-footprints",
            "title": "City building footprints",
            "description": "City records.",
            "feature_count": 30_067,
            "geometry_type": "Polygon",
            "crs": "EPSG:4326",
            "layer_type": "derived",
            "source": "City of Palm Springs GIS Department",
            "source_url": "https://example.com/source.zip",
            "source_link_label": "City CPRA release",
            "data_url": "https://example.com/buildings.geojson",
            "source_last_updated": "2026-07-29",
            "downloaded_at": "2026-07-30T00:00:00+00:00",
            "derived_from": [],
            "method": "reproject EPSG:2230 to EPSG:4326",
            "source_crs": "EPSG:2230",
            "source_archive_url": "https://example.com/source.zip",
            "contact": "GIS@palmspringsca.gov",
            "disclaimer": "Data is provided as-is.",
            "artifacts": [
                {
                    "filename": "city-building-footprints.geojson",
                    "format": "GeoJSON",
                    "url": "https://example.com/buildings.geojson",
                }
            ],
        }
    )

    readme = build_readme(catalog)

    assert "[original shapefile archive](https://example.com/source.zip)" in readme
    assert "[GIS@palmspringsca.gov](mailto:GIS@palmspringsca.gov)" in readme
    assert "[GeoJSON](https://example.com/buildings.geojson)" in readme


def test_readme_documents_census_artifacts_and_benchmark():
    catalog = derived_catalog()
    catalog["layers"].append(
        {
            "id": "neighborhood-organizations-demographics",
            "title": "Neighborhood demographics",
            "description": "Census counts.",
            "feature_count": 2,
            "geometry_type": "Polygon",
            "crs": "EPSG:4326",
            "layer_type": "derived",
            "source": "US Census Bureau",
            "source_url": "https://example.com/census",
            "source_link_label": "Census",
            "data_url": "https://example.com/demographics.parquet",
            "source_last_updated": "2020-04-01",
            "downloaded_at": "2026-07-10T12:00:00+00:00",
            "derived_from": ["neighborhood-organizations", "census-blocks-2020"],
            "method": "hybrid allocation",
            "census_vintage": 2020,
            "artifacts": [
                {
                    "filename": "demographics.parquet",
                    "format": "GeoParquet",
                    "url": "https://example.com/demographics.parquet",
                },
                {
                    "filename": "demographics.json",
                    "format": "JSON",
                    "url": "https://example.com/demographics.json",
                },
            ],
            "qa": {
                "source_totals": {"pop_total": 110},
                "unassigned_totals": {"pop_total": 10},
                "assigned_population": 100,
                "official_place_population": 101,
                "place_population_difference": -1,
                "place_population_difference_pct": -0.99,
            },
        }
    )

    readme = build_readme(catalog)

    assert "[GeoParquet](https://example.com/demographics.parquet)" in readme
    assert "[JSON](https://example.com/demographics.json)" in readme
    assert "official Palm Springs count was 101, a difference of -1 (-0.99%)" in readme
