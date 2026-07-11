# Palm Springs open data

Current public GIS layers for Palm Springs, California. Source layers are
downloaded once a week and selected derived layers combine clearly identified
open datasets. Spatial outputs use WGS84 (EPSG:4326) and are published as
GeoJSON or GeoParquet, with JSON lookup tables where useful.

## Layers

Inventory generated 2026-07-11. Feature counts describe the files on S3.
“Source updated” is the latest date reported by the upstream source when
available.

| Layer | Description | Features | Geometry | Source updated | Source |
| --- | --- | ---: | --- | --- | --- |
| [Neighborhood organizations](https://stilesdata.com/palm-springs/data/neighborhood-organizations.geojson) | Boundaries for the city's recognized neighborhood organizations. | 66 | Polygon | 2026-06-09 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/One_PS_Neighborhood_Organizations_(View)/FeatureServer/0) |
| [City boundary](https://stilesdata.com/palm-springs/data/city-boundary.geojson) | The incorporated boundary of Palm Springs. | 1 | Polygon | 2026-03-16 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/City_of_PS_Boundary/FeatureServer/0) |
| [Voting precincts](https://stilesdata.com/palm-springs/data/voting-precincts.geojson) | Voting precinct boundaries in Palm Springs. | 105 | Polygon | 2022-07-28 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/Voting_Precincts_(View)/FeatureServer/0) |
| [Parks](https://stilesdata.com/palm-springs/data/parks.geojson) | Palm Springs park boundaries. | 16 | Polygon | 2026-06-19 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/Palm_Springs_Parks_(View)/FeatureServer/0) |
| [Parcels](https://stilesdata.com/palm-springs/data/parcels.geojson) | Parcel boundaries where the source CityFlag field is Yes. | 34,304 | Polygon | 2026-06-23 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/Palm_Springs_Parcels_(View)/FeatureServer/0) |
| [Tree inventory](https://stilesdata.com/palm-springs/data/tree-inventory.geojson) | Locations and attributes from the city's tree inventory. | 15,211 | Point | 2025-12-02 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/COPS_Tree_Inventory/FeatureServer/0) |
| [Local shops](https://stilesdata.com/palm-springs/data/local-shops.geojson) | Businesses listed in the city's shop-local program. | 129 | Point | 2026-06-30 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/PS_Shop_Local_List_(View)/FeatureServer/0) |
| [Addresses](https://stilesdata.com/palm-springs/data/addresses.geojson) | Address points published by the City of Palm Springs. | 29,659 | Point | 2024-10-21 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/PS_Addresses_DroneSense/FeatureServer/0) |
| [Building footprints](https://stilesdata.com/palm-springs/data/building-footprints.geojson) | Microsoft building footprints clipped to Palm Springs and enriched with contained city address points. | 23,092 | Polygon | 2026-02-23 | [Microsoft](https://github.com/microsoft/GlobalMLBuildingFootprints) |
| [2020 Census blocks](https://stilesdata.com/palm-springs/data/census-blocks-2020.parquet) | Tabulation blocks intersecting Palm Springs with PL 94-171 population, voting-age population, race, ethnicity and housing counts. | 1,146 | Polygon | 2020-04-01 | [Census](https://www.census.gov/programs-surveys/decennial-census/about/rdo/summary-files.html) |
| [Neighborhood organization demographics](https://stilesdata.com/palm-springs/data/neighborhood-organizations-demographics.parquet) | 2020 Census population, voting-age population, race, ethnicity and housing counts apportioned to neighborhood organization boundaries. | 66 | Polygon | 2020-04-01 | [Census](https://www.census.gov/programs-surveys/decennial-census/about/rdo/summary-files.html) |
| [Voting precinct demographics](https://stilesdata.com/palm-springs/data/voting-precincts-demographics.parquet) | 2020 Census population, voting-age population, race, ethnicity and housing counts apportioned to Palm Springs voting precincts. | 105 | Polygon | 2020-04-01 | [Census](https://www.census.gov/programs-surveys/decennial-census/about/rdo/summary-files.html) |

Machine-readable metadata is available in
[`catalog.json`](https://stilesdata.com/palm-springs/data/catalog.json).

## Derived layers

### Building footprints

Built from [Microsoft GlobalML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints) and the `city-boundary`, `addresses` layers. Method: clip-to-city-boundary; attach strictly contained addresses. Licensed under [CDLA Permissive 2.0](https://cdla.dev/permissive-2-0/); [license copy](https://stilesdata.com/palm-springs/data/building-footprints-license.txt).

### Neighborhood organization demographics

Built from [US Census Bureau 2020 Decennial Census PL 94-171](https://www.census.gov/programs-surveys/decennial-census/about/rdo/summary-files.html) and the `neighborhood-organizations`, `census-blocks-2020`, `addresses`, `building-footprints` layers. Method: address share; building-area fallback; land-area fallback; full blocks retained as denominator.

Outputs: [GeoParquet](https://stilesdata.com/palm-springs/data/neighborhood-organizations-demographics.parquet) | [JSON](https://stilesdata.com/palm-springs/data/neighborhood-organizations-demographics.json).

Census vintage: 2020. Apportioned population: 44,558, compared with the official Palm Springs count of 44,575 (-17; -0.04%). Unassigned population from intersecting blocks: 3,766 (7.79%).

### Voting precinct demographics

Built from [US Census Bureau 2020 Decennial Census PL 94-171](https://www.census.gov/programs-surveys/decennial-census/about/rdo/summary-files.html) and the `voting-precincts`, `census-blocks-2020`, `addresses`, `building-footprints` layers. Method: address share; building-area fallback; land-area fallback; full blocks retained as denominator.

Outputs: [GeoParquet](https://stilesdata.com/palm-springs/data/voting-precincts-demographics.parquet) | [JSON](https://stilesdata.com/palm-springs/data/voting-precincts-demographics.json).

Census vintage: 2020. Apportioned population: 44,652, compared with the official Palm Springs count of 44,575 (+77; +0.17%). Unassigned population from intersecting blocks: 3,672 (7.60%).

## Census demographics

Demographic sidecars use 2020 Decennial Census PL 94-171 block counts for total
population, race and ethnicity, voting-age population and occupied and vacant
housing units. Blocks crossing a target boundary are apportioned by address
share, then building-footprint area when no addresses exist and finally land
area when neither inhabited feature is available. Full blocks remain the
denominator, so population outside neighborhood or precinct coverage is
reported as unassigned rather than forced into a target.

## Update the data

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make update
```

Edit [`sources.json`](sources.json) for municipal layers and
[`derived-sources.json`](derived-sources.json) for derived layers. Census
variables and targets are configured in [`census.json`](census.json). Layer IDs
must be unique lowercase kebab-case values. A failed build exits without
replacing the existing data.

Updates upload to `s3://stilesdata.com/palm-springs/data/`. If
`AWS_PROFILE_NAME` is set, the uploader uses that AWS profile; otherwise it uses
the default AWS credential chain. Override `BUCKET` or `PREFIX` when needed.

The [weekly workflow](.github/workflows/update-data.yml) runs every Monday,
uploads the current files to S3 and refreshes this inventory. It requires
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` repository secrets and can also
be run manually from the Actions tab.

Weekly runs reuse the published 2020 Census block cache. Set `CENSUS_REFRESH=1`
and provide `CENSUS_API_KEY` to rebuild that static cache from official sources.

## Source and reuse

The City of Palm Springs is the source of the municipal layers. Derived layers
identify their additional sources, methods and licenses above. Consult each
linked source for authoritative data, descriptions and applicable use terms.
