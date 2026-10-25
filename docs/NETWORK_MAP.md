> Current land filtering, zoom hierarchy, pylon heights and expanded outage sources are documented in [UK_DATA_AND_OUTAGES.md](UK_DATA_AND_OUTAGES.md). The counts and SSEN-only scope below describe the earlier source/research stage.

# Local network atlas

**This describes the previous atlas.** The current electricity-only UK node map, complete outage-history visualisation, point-weather/flood evidence and reproduction instructions are documented in [GRID_ATLAS.md](GRID_ATLAS.md). The default application now uses the cleaned `uk_power_grid.sqlite` catalog.

The default React screen is now a geographic Leaflet atlas backed by the existing read-only `data/grid/british_grid.sqlite`. The original IEEE 14-bus simulation remains at `/#demo`.

## Run

From the repository root:

```sh
.venv/bin/pip install -r requirements-map.txt
npm ci --prefix frontend
npm run build --prefix frontend
.venv/bin/python -m backend.app
```

Open `http://127.0.0.1:5001`. For frontend development run `npm run dev --prefix frontend` in a second terminal; Vite proxies `/api` to Flask. Basemap tiles need internet access and carry OpenStreetMap attribution; network records are served locally. This change does not publish the data or deploy a hosted service.

The source database must exist; reproduction details are in `data/grid/README.md`. A missing database produces an explicit API error. The atlas does not modify the database. Runtime dependencies are separate from the pre-existing deleted `requirements.txt`.

## Five display categories

1. Power stations & generation: stations, distributed generation, machine/inverter records.
2. Substations: sites and contained substation inventory records.
3. Lines, pylons & poles: line equipment/routes, supports, feeder/circuit aggregates and equivalent branches.
4. Transformers.
5. Switchgear & grid connections: breakers, switches, disconnectors, busbars, junctions/terminals, load/supply connections and ancillary grid equipment.

These groups are a UI taxonomy. Original electrical subtypes remain intact. Pylons/poles are mechanical supports, lines are physical equipment nodes, and graph edges encode relationships. The fifth category also retains miscellaneous source equipment so records are not silently discarded. A future physical-model simplification must preserve transformer, switch and terminal semantics.

## Exploration and completeness

Pan/zoom, select a source, toggle categories, click a point/route and inspect its electrical/terrain fields and raw properties. Select a relation table to distinguish topology from nonconducting membership/support and unverified candidates. Neighbor buttons traverse the graph even when the neighbor has no coordinates. Dashed connection overlays are schematic straight lines, not physical routes or proof of current energisation.

The spatial API returns at most 1,500 records per default page (hard maximum 3,000). The status explicitly reports additional records; zoom in or use Next page. Each page replaces the previous page. This is a browsable inventory, not a claim that all four million records are simultaneously visible. The complete indexed geometry bounds are queried, so a crossing line is discoverable even if its representative point is outside the viewport. Display geometries are simplified at 0.0002 degrees; detail responses preserve full geometry.

Sources remain separate to avoid depicting duplicate inventory layers as parallel circuits. Search accepts exact source-scoped asset IDs, including unlocated records. Full-text name search and automatic cross-source deduplication are not implemented. The inspector shows unknown values as unavailable, and does not invent failure probabilities. Regional weather/woodland data is documented in the training guide; it is not a nationwide live risk overlay.

Coverage is Great Britain, with missing Northern Ireland/operator coverage. Unlocated topology cannot be drawn on a map. No claim of a complete validated UK electrical network is made.

## API

- `GET /api/grid/summary`: category taxonomy, sources, saved dataset counts and caveats.
- `GET /api/grid/assets?bbox=west,south,east,north&source=OSM&categories=generation,substation&limit=1500&after=0`: GeoJSON with an opaque rowid continuation `next_after`; reuse identical filters for subsequent pages. Invalid bounding boxes/categories/limits return 400.
- `GET /api/grid/asset?id=...&relation=edges&offset=0`: full asset fields/source properties and up to 200 neighbors, with `next_offset`. Also accepts `relationships` or `candidate_edges`; these are distinct tables.
- `GET /api/grid/search?id=...`: exact ID lookup, including unlocated equipment.

All user values are bound SQL parameters; relation table names use a fixed allowlist. Database access is read-only. See `MODEL_TRAINING.md` for the prediction target, features, risk relationships, validation design and database recommendation.
