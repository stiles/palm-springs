# Changelog

## Unreleased

### Added

- Daily SERCC maximum-temperature normals in JSON and CSV, with a 365-day
  initial backfill and seven-day correction window (2026-07-11).
- City of Palm Springs building footprints received through a California Public
  Records Act request, published in their original format and as WGS84 GeoJSON
  and GeoParquet (2026-07-30).

### Fixed

- Preserve published climate history without failing when SERCC reports no new
  station observations during the refresh window (2026-07-30).
