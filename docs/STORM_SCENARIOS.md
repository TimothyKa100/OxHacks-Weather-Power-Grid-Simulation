# Portable storm scenarios

Open `/#storm`, choose **Synthetic storm**, press **Place in ocean**, then tap
the ocean and a destination direction. The circular handle can be dragged or
rotated with arrow keys. Numeric coordinates provide a keyboard alternative.
The map includes extra Atlantic space. Pinch/drag navigation still works.

**Simulate storm** runs one failure/repair realization. **Compare mitigation**
runs the existing multi-day ensemble and reinforcement planner. Both execute on
the server. Playback and scrubbing use the returned hourly states and perform no
inference or network requests. Settings change the next run; exports and mitigation
reruns retain the completed run's exact storm configuration and seed.

## Module boundaries

| Module | Responsibility | Dependencies |
| --- | --- | --- |
| `backend/storm_scenarios.py` | Versioned config validation, track, six coherent hourly weather variables and 72-hour background | NumPy, Python standard library |
| `backend/scenario_adapter.py` | Shared geographic weather cells → route/pylon engineering risk → single-run grid simulation | Existing grid/weather/physics modules |
| `backend/scenario_jobs.py` | Bounded queue, status, deduplication, result expiry | Python standard library |
| `failure_model/scenarios.py` | Synthetic weather → existing ordered feeder feature contract | Existing optional model dependencies and FeatureBuilder |
| `frontend/src/storm-scenario/config.js` | Defaults, validation, track preview and direction math | None |
| `frontend/src/storm-scenario/client.js` | Async HTTP client, injected base URL/fetch, polling, timeout and abort | Browser Fetch/Abort APIs |
| `frontend/src/storm-scenario/useStormScenario.js` | Config and two-tap placement state | React |
| `frontend/src/storm-scenario/StormControls.jsx` | Controlled, styled editor | React and config module |
| `frontend/src/storm-scenario/StormTrackOverlay.jsx` | Track, core footprint and accessible draggable direction handle | React, caller-supplied SVG projection |
| `frontend/src/App.jsx`, `UkMap.jsx` | Integration into this dashboard | Existing application |

To merge into a similar frontend, copy `frontend/src/storm-scenario/`. Its modules
have no imports from the host app, map library, router or global state. The CSS is
scoped to `storm-editor`/`storm-track`, apart from an optional map placement cursor.
For Leaflet, use its map click and projection APIs in place of the SVG overlay;
keep the config, HTTP client, controls and backend unchanged. Ocean rejection is
an adapter responsibility: the current map uses its existing land path, with no
external reverse-geocoding requests. Numeric/API origins can be on land for other
scenario workflows.

```jsx
import {useStormScenario} from './storm-scenario/useStormScenario.js';
import StormControls from './storm-scenario/StormControls.jsx';
import {createStormClient} from './storm-scenario/client.js';

const client = createStormClient({baseUrl: '/api/storm-scenarios'});

function MyStormEditor() {
  const storm = useStormScenario();
  // In your map's click callback:
  // storm.place([event.longitude, event.latitude], isLand);
  // In your run handler:
  // const run = await client.run({storm_config: storm.config, crews: 6},
  //   {mode: 'single', signal: abortController.signal, onStatus: setStatus});
  return <StormControls value={storm.config} onChange={storm.setConfig}
    placement={storm.placement} onPlacementChange={storm.setPlacement}
    message={storm.message}/>;
}
```

For a separate frontend origin, configure a same-origin API proxy or deliberately
configured CORS on that deployment. The client doesn't assume a hostname or port.

## Version 1 configuration

`GET /api/storm-scenarios` returns authoritative defaults, numeric limits and
presets. Configs reject unknown fields. Missing fields use these defaults:

```json
{
  "version": 1,
  "origin": {"lon": -10, "lat": 50.5},
  "bearing_deg": 45,
  "speed_kmh": 35,
  "radius_km": 150,
  "peak_gust_kmh": 160,
  "hours": 36,
  "preset": "single_peak",
  "seed": 1,
  "start_time": "2026-01-01T00:00:00+00:00",
  "initial_soil_moisture": 0.3
}
```

- Coordinates are WGS84 longitude/latitude. Origin bounds: 25°W–15°E, 45–65°N.
- Direction is travel **toward** a bearing, clockwise from north. Generated wind
  direction is meteorological **from**, also clockwise from north.
- Speed: 5–100 km/h; Gaussian core radius: 30–400 km; reference peak gust:
  60–250 km/h; duration: 6–72 whole hours; seed: integer 0–2147483647.
- Presets: `single_peak`, `persistent`, `double_peak`. The last has a second,
  trailing spatial front. The radius describes the cross-track Gaussian sigma;
  the along-track sigma is 1.4 times the radius.
- Soil moisture: 0.05–0.55 m³/m³. `start_time` requires an aligned hour with an
  explicit timezone, normalized to UTC. Season changes background temperature.
- When used with `/api/storm-plan`, `storm_config` is authoritative for peak,
  duration, preset and seed; legacy top-level aliases remain for old clients.

## HTTP interface

Submit a short run:

```http
POST /api/storm-scenarios/runs
Content-Type: application/json

{"mode":"single","storm_config":{"hours":12,"preset":"persistent"},"crews":6}
```

The response is `202` with `job_id` and `status_url`, also provided in the
`Location` header. Poll `GET /api/storm-scenarios/runs/{job_id}`:

```json
{"job_id":"…","status":"queued"}
{"job_id":"…","status":"running"}
{"job_id":"…","status":"complete","result":{"network":{},"config":{},"frames":[],"metrics":{},"replay":{},"synthetic_weather":{}}}
```

Failed jobs return `status: "failed"` and an error message. Invalid submissions
return `400`, a full queue returns `429`, and unknown/expired IDs return `404`.
Run/status responses use `Cache-Control: no-store`. Repeated normalized requests
reuse pending/completed jobs while retained. The client polls every 600 ms, stops
after five minutes, and supports caller cancellation. Aborting the client stops
polling; server work finishes and remains reusable until expiry.

For an ensemble, submit `mode: "compare"` with the existing `/api/storm-plan`
request fields, including `storm_config`, `samples`, `budget`, `crews`,
`custom_lines` and `economics`. Historical and imported GB profiles also work in
this comparison mode. The strict IEEE14 probability-import endpoint remains the
existing synchronous API. A full comparison preserves the existing `plan` and
replay response contract. Example:

```json
{"mode":"compare","scenario":"synthetic","storm_config":{"hours":12},"samples":16,"budget":5,"crews":6}
```

The single-run realization and the ensemble's selected demonstration realization
can have different failure outcomes. They share the same weather when config is
identical. Applying mitigation uses the ensemble's existing paired draws.

## Weather and feature integration

`generate_weather(config, [[lon, lat], ...])` returns NumPy arrays with dimensions
`(72 + hours + 1, number_of_locations)`, hourly UTC timestamps and centre tracks.
The final row supports an end cutoff. The six variables are:

| Variable | Units / timing |
| --- | --- |
| `wind_gusts_10m` | km/h, instantaneous |
| `wind_speed_10m` | km/h, instantaneous, below gust |
| `wind_direction_10m` | Integer degrees from north, instantaneous |
| `precipitation` | mm over completed interval `[t−1h,t)`, sampled at its midpoint |
| `temperature_2m` | °C, seasonal/daily background and storm anomaly |
| `soil_moisture_0_to_7cm` | m³/m³, bounded rain/drainage bucket |

Before storm start, 72 hours of background supply complete rolling histories.
There is zero background rain in v1; initial wetness represents antecedent soil
conditions, not a reconstructed wet-week rain record. The field is deterministic
for config and location, independent of batching or location ordering.

The geographic simulator rounds route weather locations to 0.1° shared cells,
then applies existing terrain factors only in the engineering adapter. Pylon
hazards sum to line risk using the current fragility assumptions. Wind governs
engineering failure and safe repairs; generating temperature/soil variables does
not imply the engineering model has learned their effect on failures.

For the feeder ML contract, reuse existing asset/route data:

```python
from failure_model.features import FeatureBuilder
from failure_model.scenarios import scenario_features

builder = FeatureBuilder('data/failure_model/v1', 'data/processed/weather')
table = scenario_features(builder, {'hours': 24, 'preset': 'persistent'})
# Arrow table: asset_id, cutoff_utc, existing ordered FEATURES, horizon_hours.
# Metadata mode=synthetic_scenario. Cutoffs are [start, start+hours).
```

This adapter uses route length weights, bearing envelopes, all existing rolling
features and static asset properties. It enables synthetic weather coverage on
a copy of the builder, leaving historical availability unchanged. Missing static
properties remain missing. It never calls historical-only model inference,
claims a trained model exists, or maps feeder predictions to transmission lines.

## Resource limits and deployment

The browser only handles small configuration changes, SVG track drawing and
30 FPS playback, paused when the tab is hidden. The static grid layers and editor
retain memoization. Full
weather histories and feature matrices stay on the server; geometry is sent once
per run, never per animation frame. No new frontend or runtime dependencies were
added. The optional ML adapter uses `requirements-model.txt`.

The demo queue has one worker, at most two pending/running jobs, at most four
retained jobs and a ten-minute completed-result lifetime. Context/plan caches
each retain four entries. Simulation work shares the existing experiment lock.
Use **one Flask application process** for this in-memory queue; threads are fine.
Process restarts lose jobs. For several application processes/replicas, replace
`ScenarioJobs` with a shared queue/result store (or use sticky routing with its
limitations). The config, frontend and weather generator need no changes.

The field and soil bucket are illustrative, and the track uses a local
equirectangular approximation. These limitations are separate from device
performance. Extending the weather model is isolated to `storm_scenarios.py`.

## Verification

```bash
.venv/bin/python -m pytest tests/test_storm_scenarios.py tests/test_map_planning.py -q
cd frontend
npm test
npm run build
```

For desktop/mobile interaction checks, with the built Flask app running and
Playwright/Chrome available:

```bash
.venv/bin/python scripts/check_storm_browser.py --url http://127.0.0.1:5001
```
