# Palm Springs open data

Current copies of public GIS layers published by the City of Palm Springs,
California. The data is downloaded from ArcGIS once a week and converted to
GeoJSON in WGS84 (EPSG:4326).

## Layers

Inventory generated 2026-07-10. Feature counts describe the files on S3.
“Source updated” is the edit date reported by ArcGIS when available.

| Layer | Description | Features | Geometry | Source updated | Service |
| --- | --- | ---: | --- | --- | --- |
| [Neighborhood organizations](https://stilesdata.com/palm-springs/data/neighborhood-organizations.geojson) | Boundaries for the city's recognized neighborhood organizations. | 66 | Polygon | 2026-06-09 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/One_PS_Neighborhood_Organizations_(View)/FeatureServer/0) |
| [City boundary](https://stilesdata.com/palm-springs/data/city-boundary.geojson) | The incorporated boundary of Palm Springs. | 1 | Polygon | 2026-03-16 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/City_of_PS_Boundary/FeatureServer/0) |
| [Voting precincts](https://stilesdata.com/palm-springs/data/voting-precincts.geojson) | Voting precinct boundaries in Palm Springs. | 105 | Polygon | 2022-07-28 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/Voting_Precincts_(View)/FeatureServer/0) |
| [Parks](https://stilesdata.com/palm-springs/data/parks.geojson) | Palm Springs park boundaries. | 16 | Polygon | 2026-06-19 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/Palm_Springs_Parks_(View)/FeatureServer/0) |
| [Parcels](https://stilesdata.com/palm-springs/data/parcels.geojson) | Parcel boundaries where the source CityFlag field is Yes. | 34,304 | Polygon | 2026-06-23 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/Palm_Springs_Parcels_(View)/FeatureServer/0) |
| [Tree inventory](https://stilesdata.com/palm-springs/data/tree-inventory.geojson) | Locations and attributes from the city's tree inventory. | 15,211 | Point | 2025-12-02 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/COPS_Tree_Inventory/FeatureServer/0) |
| [Local shops](https://stilesdata.com/palm-springs/data/local-shops.geojson) | Businesses listed in the city's shop-local program. | 129 | Point | 2026-06-30 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/PS_Shop_Local_List_(View)/FeatureServer/0) |
| [Addresses](https://stilesdata.com/palm-springs/data/addresses.geojson) | Address points published by the City of Palm Springs. | 29,659 | Point | 2024-10-21 | [ArcGIS](https://services.arcgis.com/f48yV21HSEYeCYMI/ArcGIS/rest/services/PS_Addresses_DroneSense/FeatureServer/0) |

Machine-readable metadata is available in
[`catalog.json`](https://stilesdata.com/palm-springs/data/catalog.json).

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
