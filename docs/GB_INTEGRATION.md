# GB map and multi-day mitigation integration

## Branch comparison

Source map: `feature/gb-storm-map` at `f0627f4`.
Source planner: `feature/multiday-weather-recovery` at `9440482`.

The map branch was already eight commits ahead of the planner: it retained the
backend planner but replaced the planner frontend with a standalone storm animation.
This integration preserves the geographic network, sources/attribution, historical
storm builder, observed-outage chart and legacy endpoint. It reconnects the primary
map UI to the multi-day decision logic and restores the public/organisation planner.
The source branches are left unchanged.

## Request and response

`POST /api/storm-plan` accepts:

- `scenario`: `synthetic`, a listed historical storm ID, or `imported` with `weather`.
- `seed` (0–2147483647), `samples` (16–128), `budget` (0–12), `crews` (1–40).
- Synthetic-only `peak_gust` (60–250 km/h), `hours` (6–72), `storm_type`
  (`single_peak`, `persistent`, `double_peak`). Historical records determine their
  own duration/exposure. Imports accept 1–72 hourly records.
- Optional `custom_lines`, `economics`, and `weather` using existing validation.

The response retains map `network`, settled `frames`, historical context where
available, and adds `plan` (all portfolios, cascade playback, decisions, costs,
training trace and uncertainty) and `replay`. Send `replay.request` back to this
endpoint with changed `custom_lines` or economics to compare the same event.
Scenario settings in the picker are not silently substituted during mitigation.

Synthetic and historical route/pylon hazards enter as an `engineering_model`
profile named `route-pylon-v1`. No fitted ML model or training cutoff is invented.
ML profiles still require ordered branch IDs and prediction provenance. Imported
weather without geographic fields has no fabricated storm centre or observed cuts.
Full precision line probabilities are preserved, including tiny historical risks.

The physical engine uses limited crews, weather-gated repairs and hourly load
reconnection from the multi-day branch. This replaces the primary animation's
permanent pylon damage and automatic next-hour overload reclose rule. The old
`/api/storm` endpoint and bundled sample keep their documented legacy behavior.
A supplied file is bounded by the server's 4 MiB request limit.

## Display and interpretation

The decision table selects the portfolio displayed on the map. Green routes identify
hardening; selecting a route opens its planning identity in the asset picker. The
map displays each hour's settled state and allows pause/scrub. Full cascade rounds
are preserved in exported portfolio playback. No exact failed pylon location is
assigned by the multi-day engine.

Historical outage dots and risk timing chart stay unchanged across hypothetical
plans. They compare distribution records and an engineering wind-risk proxy, not
measured transmission damage or intervention success. Supplied outage labels never
enter investment selection. Model selection uses simulation training draws; this is
not ML training on observed incidents.

Public advice remains general: no home-specific outage prediction or safe pedestrian
route is inferred. Repair preparation changes queue order only, without crew travel.
Candidate screening on the GB network evaluates the 24 most frequently failing
training assets; other branches can still be added manually.

## Running and validation

```bash
.venv/bin/python -m pytest -q
npm test --prefix frontend
npm run build --prefix frontend
.venv/bin/python -m backend.app
```

Large-network evaluation uses workers where permitted, with identical serial
fallback when the host blocks worker IPC. This keeps the demo usable on restricted
hosts without masking electrical solve failures. The API caches bounded complete
requests. Repeating or customizing a plan uses identical sources and seeds.
