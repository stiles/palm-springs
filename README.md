# Palm Springs GIS data

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
2026-07-11; “source updated” is the latest date reported by the upstream
publisher when one is available.

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

Want to work with the inventory programmatically? Start with
[`catalog.json`](https://stilesdata.com/palm-springs/data/catalog.json).

## Derived layers

### Building footprints

We start with [Microsoft GlobalML Building Footprints](https://github.com/microsoft/GlobalMLBuildingFootprints), clip the footprints to the city boundary and attach any city address points that fall inside each building. The source data is available under [CDLA Permissive 2.0](https://cdla.dev/permissive-2-0/); a [copy of the license](https://stilesdata.com/palm-springs/data/building-footprints-license.txt) travels with the output.

### Neighborhood organization demographics

This estimate starts with [US Census Bureau 2020 Decennial Census PL 94-171](https://www.census.gov/programs-surveys/decennial-census/about/rdo/summary-files.html) block counts. We combine them with `neighborhood-organizations`, `census-blocks-2020`, `addresses`, `building-footprints`, using addresses and buildings to place people more realistically than a simple land-area split.

Download it as [GeoParquet](https://stilesdata.com/palm-springs/data/neighborhood-organizations-demographics.parquet) | [JSON](https://stilesdata.com/palm-springs/data/neighborhood-organizations-demographics.json).

The 2020 Census produced an apportioned population of 44,558 here. The official Palm Springs count was 44,575, a difference of -17 (-0.04%). Another 3,766 people from intersecting blocks remain unassigned (7.79%) rather than being forced into a boundary.

### Voting precinct demographics

This estimate starts with [US Census Bureau 2020 Decennial Census PL 94-171](https://www.census.gov/programs-surveys/decennial-census/about/rdo/summary-files.html) block counts. We combine them with `voting-precincts`, `census-blocks-2020`, `addresses`, `building-footprints`, using addresses and buildings to place people more realistically than a simple land-area split.

Download it as [GeoParquet](https://stilesdata.com/palm-springs/data/voting-precincts-demographics.parquet) | [JSON](https://stilesdata.com/palm-springs/data/voting-precincts-demographics.json).

The 2020 Census produced an apportioned population of 44,652 here. The official Palm Springs count was 44,575, a difference of +77 (+0.17%). Another 3,672 people from intersecting blocks remain unassigned (7.60%) rather than being forced into a boundary.

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
[SERCC Climate Perspectives](https://sercc.oasis.unc.edu/Map.php?region=wrcc) station
`048892`, at Palm Springs International Airport. It calculates the reported normal by subtracting the
departure from the observed maximum.

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
