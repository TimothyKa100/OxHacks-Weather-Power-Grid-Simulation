> Current land filtering, zoom hierarchy, pylon heights and expanded outage sources are documented in [UK_DATA_AND_OUTAGES.md](UK_DATA_AND_OUTAGES.md). The counts and SSEN-only scope below describe the earlier source/research stage.

The default application now has two interactive views: an electricity-only network atlas and the full locally available outage register. The working model is `data/grid/uk_power_grid.sqlite`. Original source snapshots remain available for audit.

**Open the visualisations**

Run `.venv/bin/python -m backend.app`, then open:

- Network: `http://127.0.0.1:5001/#network`
- Outage history: `http://127.0.0.1:5001/#outages`
- The independent illustrative IEEE experiment remains at `/#demo`.

The current local review server uses port 5055. Screenshots and browser checks are under `docs/visualisations/`.

Saved previews: [network nodes](visualisations/network-nodes.png), [outage history](visualisations/outage-history.png), [node conditions](visualisations/node-conditions.png), and [mobile outage view](visualisations/outage-mobile.png).

Validation passed: 96 Python tests, production frontend build, full-catalog integrity/count/reference checks and desktop/mobile Chrome checks. The browser recorded zero JavaScript/API errors and verified that map aggregates represent all 4,121,148 located nodes and 69,386 located outage reports. Machine-readable results are in `data/grid/reports/atlas_validation.json` and `docs/visualisations/browser-validation.json`.

The country overview represents **every located record** with counted clusters. Zooming reveals individual node points. Dense or colocated equipment is aggregated without dropping records. The source selector exposes overlapping inventories; these are source relrepresentations, not a deduplicated physical-asset census. Unlocated nodes can be browsed by ID instead of receiving invented coordinates. Complete bounds include two mapped international interconnector endpoints.

Select a component to inspect its electrical fields, source data and historical point conditions. Asset names prominently include their component type. An operator-listed substation serving a shop remains a substation. The basemap omits shop POI labels; its contextual water/place labels are separate from the asset dataset.

The history view includes all 76,574 downloaded SSEN HV reports from April 2015 through October 2026, including 6,990 reports with possible operational/planned wording. Planning status remains unknown. Use the timeline, dates, region, reported cause, report group and location filters. The incident register includes records without coordinates. A candidate location is a feeder or GIS circuit representative point, not a verified failed component.

**What was cleaned**

An explicit taxonomy retains substations, transformers, plants/distributed generation, storage, lines, breakers/switchgear, supports and necessary electrical topology/aggregate nodes. It rejects consumer sockets/outlets, charging-point values, manual/hydraulic mechanisms, gas equipment, railway-only support tags and ambiguous nonstandard values. Lifecycle exclusions are retained. Gantries, circuit aggregates and switchgear previously labelled miscellaneous are classified explicitly; battery/pumped-storage technical fields identify storage, and switch tags identify circuit breakers/disconnectors. Name keywords alone never remove a published electrical asset.

The derived catalog retains 4,232,027 source-scoped grid records, of which 4,121,148 have coordinates. It removes 2,940 unrelated or uncertain source records and corrects 11,995 classifications. Exclusions include solar hot-water collectors, heat pumps and heating-only records without electrical evidence; explicitly tagged electricity-producing combined heat and power remains eligible. The Northern Ireland import adds 219,554 nonduplicate power/topology records before the final filter, leaving 219,547 retained NI records. The checksum-verified Geofabrik extract is clipped using the mapped NI administrative boundary. Mapped incidence remains separate from operator-certified electrical connectivity. Sharing a support does not connect all circuits.

Audit outputs:

- `data/grid/reports/cleaning.json`
- `data/grid/normalized/excluded_non_grid_nodes.parquet`
- `data/grid/normalized/grid_reclassification_audit.parquet`
- `data/grid/reports/northern_ireland.json`

**Node locations and environmental evidence**

All new environmental associations use node point coordinates. Lines use their stored point representation; the atlas does not average weather along routes. Source location methods identify mapped points, representative sites/areas and unlocated equipment. Historical geometry/configuration remains incomplete.

`node_weather_links.parquet` links each node to the nearest actually downloaded ERA5 grid point when it is within 35 km. More distant or unlocated nodes remain unavailable. The 0.25-degree reanalysis is a coarse historical estimate, not an instrument at the asset or a live forecast. The API returns sample time, returned grid coordinates, distance, wind speed/gust/direction, temperature and preceding-hour precipitation. Six ambiguous incident UTC conversions remain unresolved. UTC assumes Europe/London because the incident source does not publish its timezone.

The collection currently supports nearby weather estimates for 1,655,254 node records; 2,465,894 located records lack a sample within 35 km, and 110,879 records are unlocated. All 69,380 located/time-resolved outage reports receive conditions at their candidate point and provisional reported hour. This does not confirm weather at actual failure onset or location. A node-only 0.25-degree sampling plan lists 845 request locations; 77 matching native cells are cached. It is a collection plan, not newly downloaded nationwide weather. Previous provider daily quota limits still constrain further acquisition.

The [Environment Agency Recorded Flood Outlines](https://environment.data.gov.uk/dataset/8c75e700-d465-11e4-8b5b-f0def148f590) add dated flood-footprint exposure for England. The adapter reads the publisher's GeoPackage through GDAL to linearize curved geometry, checks CRS, and intersects node points in EPSG:27700. It preserves source date ranges, quality and attribution. The 31,744 outlines link to 91,474 node points across all recorded dates. Date ranges can be imprecise; an outline is not proof of internal equipment inundation, flood depth, physical breakage or causal attribution. No matching record means unknown, not no flooding. The archive does not cover the rest of the UK.

There are currently zero source-date flood matches at the candidate outage proxy points. That is not evidence that those outages were flood-free: their actual failed-component locations are unavailable. Flood exposure cannot be validated as a component-failure predictor from these proxy joins.

Environmental/data exports:

- `normalized/clean_grid_node_locations.parquet`: every retained node's location, type and typed electrical properties.
- `normalized/node_weather_links.parquet`: one association/status per node.
- `normalized/node_weather_sampling_plan.parquet`: native-grid request plan for node points.
- `normalized/all_outage_reports.parquet`: every downloaded report, candidate location, sampled conditions, uncertainty and eligibility fields.
- `normalized/recorded_flood_outlines.parquet`: publisher attributes and BNG geometry.
- `normalized/node_recorded_flood_history.parquet`: point-to-footprint overlaps across recorded dates.
- `reports/node_conditions.json`, `reports/recorded_floods.json`, `reports/atlas_validation.json`.

These files are under `data/grid/`. `failed_asset_id` stays null and every report remains ineligible as a verified physical-breakage target. Actual component IDs, onset/restoration, planned status, operating exposure and historical identity are still required to train that model. Reported causes and weather associations alone do not establish causality. Existing feeder research features remain separate from this point-based atlas.

**Reproduce**

From the repository root:

```bash
.venv/bin/python -m pip install -r requirements-grid-prep.txt
.venv/bin/python scripts/grid_northern_ireland.py
.venv/bin/python scripts/clean_power_grid.py
.venv/bin/python scripts/prepare_grid_conditions.py
.venv/bin/python scripts/grid_recorded_floods.py
.venv/bin/python scripts/validate_grid_atlas.py
npm run build --prefix frontend
.venv/bin/python -m pytest -q
```

The source GB database and regional source/weather files are dependencies; the earlier source collection scripts are described in `data/grid/README.md`. NI/flood downloads require public network access. Download hashes, source URLs, licence and attribution are recorded. No private operator records or access controls are bypassed. The atlas serves data read-only; preparation scripts write a separate working catalog.

**API**

- `/api/grid/summary`: retained types, counts, sources, complete point bounds and provenance.
- `/api/grid/overview?bbox=west,south,east,north&zoom=6&categories=...&source=...`: complete counted node overview. Coarse views count complete 0.05-degree cells intersecting the viewport; detailed views use exact point bounds. Aggregation adapts until the response fits; it never silently discards records.
- `/api/grid/node-list?bbox=...&offset=0`: 50 point records per page from a selected cluster.
- `/api/grid/unlocated?offset=0`: browse nodes without coordinates.
- `/api/grid/find?q=...`: exact ID or escaped name-prefix search.
- `/api/grid/asset?id=...`: detailed retained component and source-specific connections.
- `/api/grid/weather?id=...&at=2021-11-26T12:00:00Z`: node-point estimate plus recorded flood history/date-range matches.
- `/api/grid/outages/summary`: all matching history totals, monthly counts and reported causes.
- `/api/grid/outages/overview?bbox=...&zoom=6`: complete counted map of located reports.
- `/api/grid/outages?offset=0&limit=50`: paginated register, including unlocated records.
- `/api/grid/outage?id=...`: original report evidence and conditions.

History filters are `start`/`end` (inclusive published local dates), `operator`, `cause`, `classification` and `location=all|mapped|unmapped`. Malformed ranges/bounds are rejected; SQL values are bound parameters. The legacy geometry/pagination `/assets` endpoint remains for compatibility, while the new default map uses complete point overviews.

**Sources and attribution**

- [OpenStreetMap electricity taxonomy](https://wiki.openstreetmap.org/wiki/Key:power), © OpenStreetMap contributors, ODbL; [Geofabrik Ireland extract](https://download.geofabrik.de/europe/ireland-and-northern-ireland.html), clipped to [NI relation 156393](https://www.openstreetmap.org/relation/156393).
- Existing operator/CIM/LTDS/NESO/DESNZ inventories retain source-scoped IDs and original attribution in `data/grid/ATTRIBUTION.md`.
- ERA5/Copernicus via Open-Meteo, source parameters/units/hashes in `data/weather_manifest.json`.
- Recorded flood footprints: © Environment Agency copyright and/or database right 2025. All rights reserved; Open Government Licence v3.0. [Publisher guidance](https://environment.data.gov.uk/file-management-open/data-sets/ed73f2e8-a3c2-44db-952d-6e359c7c3987/files/Guidance_Recorded_Flood_Outline_v6_2.pdf).
- Context basemap: Esri, HERE, Garmin, © OpenStreetMap contributors and GIS user community; the label/reference overlay is not used.
