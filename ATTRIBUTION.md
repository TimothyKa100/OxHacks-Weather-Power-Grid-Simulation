# Attribution and reuse review

## IC Hack 2026 WildFire

- Source: https://github.com/ltanak/ICHack26
- Inspected revision: `dac1afe03655081b6633ba3eeb07f45db86834aa` (HEAD when inspected on 24 October 2026).
- Repository licence: **MIT**, copyright © 2026 Louis Tanak. The licence permits use, modification and distribution subject to preserving its copyright and permission notice. The complete upstream notice is retained in `LICENSE` and `third_party/ICHack26-LICENSE`.
- Inspected files: root `README.md`, `LICENSE`; `backend/app.py`, `backend/pixi.toml`, `backend/historical_sim/simulation.py`; `frontend/package.json`, `frontend/src/components/WildfireSimulation.js`, `WildfireMitigation.js`, and `frontend/src/store/api.js`.
- Reused architectural ideas: Flask JSON endpoints around a Python/NumPy step simulation; a separate React interface; explicit parameter controls; step playback; Monte Carlo failure-frequency aggregation; mitigation followed by a before/after comparison.
- GridGuard is an architectural adaptation with newly implemented application code, not a claim to reproduce the winning project's wildfire prediction performance. The electrical solver, island dispatch, cascades, training/held-out optimization and SVG network are new. Broad framework idioms remain analogous; no trained weights or wildfire datasets were transferred.
- Original Next/React frontend uses map and canvas components. GridGuard keeps React 18 and Flask while substituting Vite and SVG for a simpler single-screen, locally served demo. Redux, satellite imagery, Anthropic calls, fire percolation rules and tree-clearing interventions are removed.
- The upstream Git fetch succeeded, but checkout on Windows hit existing `:Zone.Identifier` filenames. Selected source files were inspected directly at the pinned revision; those invalid filenames and extraneous data are omitted from the deliverable.

No endorsement by the upstream authors, IC Hack or HRT is implied.

## IEEE 14-bus / pandapower

`data/ieee14.json` was generated from `pandapower.networks.case14()` in pandapower 3.5.6. Pandapower documents the case's origin through PYPOWER/MATPOWER from the IEEE Common Data Format benchmark. See:

- https://pandapower.readthedocs.io/en/stable/networks/power_system_test_cases.html#case-14
- https://github.com/e2nIEE/pandapower
- https://matpower.org/

Pandapower is BSD-3-Clause; its installed licence is retained as `third_party/pandapower-LICENSE`. The export includes original bus active loads, branch endpoints/reactances/taps, the two enabled active generators' limits, and intact reference flows. The installed `rundcpp` internal ppc output uses an effectively unconstrained external-grid limit, so the export explicitly restores the original `net.ext_grid.max_p_mw` (332.4 MW). This is covered by a regression assertion. Original zero-active-P generator buses are explicitly disabled as active sources in this model.

Synthetic layout, exposure, costs, inferred ratings and hardening effects are GridGuard additions and must not be represented as original IEEE measurements.

## Great Britain transmission network (uk-grid-atlas / OpenStreetMap)

`data/gb_network.json` is built by `scripts/build_gb_network.py` from https://github.com/jacobwright32/uk-grid-atlas (`src/data/gb/transmission.json`, `stations.json`, `meta.json`; atlas data generated 2026-07-21). The atlas code is MIT, © 2026 Jacob Wright. The underlying power data is **© OpenStreetMap contributors, available under the Open Database Licence (ODbL)**: https://www.openstreetmap.org/copyright. `data/gb_network.json` is a derived database and is likewise offered under the ODbL; the map in the dashboard displays this attribution.

Derived from OSM: line routes and voltage classes (132/275/400 kV), substation positions (way endpoints clustered at 2 km), circuit counts and route lengths, and generating-station locations, fuels and stated capacities. GridGuard additions, not OSM or National Grid data: per-km reactances, thermal ratings, reference dispatch, demand allocation (major urban areas plus a uniform base), hardening costs and storm exposure. The graph is not the operational GB network model: transformers, switching arrangements, embedded generation and interconnector flows are omitted.

## Elevation (EU-DEM via OpenTopoData)

Terrain heights for every substation and for route sample points every ~2 km (`ele_m`, `samples`, `ele_max_m`, `ele_mean_m` in `data/gb_network.json`) come from **EU-DEM v1.1** (25 m), produced by the Copernicus Land Monitoring Service with funding from the European Union, queried through the public OpenTopoData API (https://www.opentopodata.org/datasets/eudem/). Raw responses are cached in `data/gb_elevation_cache.json` so rebuilds need no network. Heights are of the terrain at the route sample point, not of individual surveyed pylon sites; sea and no-data points are treated as 0 m.

## Real storm benchmarks (outages and weather)

`data/storms/<id>.json` files are built by `scripts/build_storm_benchmark.py` from `data/outages/unplanned_outages.csv` (the processed companion outage dataset). Storm names and impact dates follow the Met Office UK Storm Centre season pages.

- **Outages:** unplanned power cuts from the git-scraped NGED feed (jmcharter/nationalgrid-outage-history, from May 2024) and the UK Power Networks `ukpn_powercuts` feed (from late October 2025). Each storm uses the feeds that ran for its whole window, so only those operators' licence areas are covered. The 2021–22 Outage-Watch rows in the CSV are too sparse to give a normal rate and are not used. NGED start times are first-snapshot sightings (the scraper polled roughly every 20 min to 3 h). Rows whose start is unknown because they were first seen already restored are dropped. Incidents that restart at the same location within 4 h of another ending there are treated as the feed re-issuing an id and dropped (for Storm Darragh, 1,286 in the storm window, including a 1,102-incident batch at 22:03 UTC on 8 Dec 2024).
- **Weather:** hourly 10 m wind gusts from the Met Office UKV model (`ukmo_seamless`) via the Open-Meteo historical forecast API, sampled on a 0.25° grid. Weather data by Open-Meteo.com, licensed CC BY 4.0. Met Office data © Crown copyright, Open Government Licence.

## UK outline

`frontend/src/uk-outline.json` is the GBR feature of Natural Earth's 1:50m Admin 0 countries (public domain, https://www.naturalearthdata.com/), simplified and pre-projected for the map.

## Browser runtime dependencies

The built browser app includes React, ReactDOM and Scheduler under their MIT licences, retained in `third_party/`. Other packages are installed from their upstream distributions with their own licences; the Python environment and Node dependency tree are excluded from the downloadable release.

## Weather and news

The sample weather profile is authored for this demo. There is no live forecast, GDELT feed, geographic utility map or verified outage observation. GDELT appears only as a documented next-step integration, with a link to official API documentation.
