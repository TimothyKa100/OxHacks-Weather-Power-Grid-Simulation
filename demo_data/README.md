# Demo data

Everything the simulation and the demo need, copied from `data/` by
`python scripts/build_demo_data.py`. `manifest.json` lists each file with its purpose, size
and SHA-256. The layout matches `data/`, so the backend reads either (`backend/paths.py`;
override with `GRIDGUARD_DATA`).

| Path | Used for |
|---|---|
| `gb_network.json`, `ieee14.json` | Simulation networks |
| `sample_weather.json`, `sample_storm.json` | Legacy weather profile; offline frontend fallback |
| `storms/` | Historical storm replays |
| `grid/detail_graph.json` | National 33–400 kV graph, reduced to view networks |
| `grid/asset_index.parquet` | Map asset level-of-detail layer |
| `failure_model/models/` | Corridor models v1 and v2 (hybrid ML input) |
| `failure_model/exports/` | Archived complete-feature predictions for replays |
| `examples/` | Example uploads |

`view_networks/` is a cache written at run time. The grid-derived files and locally built
storms are not committed. They come from sources with mixed licences (see
`grid/ATTRIBUTION.md`), so rebuild them locally with the script.
