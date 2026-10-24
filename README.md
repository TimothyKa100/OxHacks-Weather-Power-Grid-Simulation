# GridGuard

A storm crossing the Great Britain transmission grid, one hour at a time: wind → pylons → lines → power flow.

- **Grid:** 303 substations and 367 lines at 132–400 kV, built from [uk-grid-atlas](https://github.com/jacobwright32/uk-grid-atlas) OpenStreetMap data.
- **Pylons:** each line's pylon count is its route length ÷ a typical span (400 kV 360 m, 275 kV 350 m, 132 kV 280 m), about 33,000 in total.
- **Elevation:** every substation and a point every ~2 km along each real OSM route carry terrain height from EU-DEM (25 m). Local gust is scaled by the UK National Annex to BS EN 1991-1-4 altitude factor, `c_alt = 1 + 0.001 × elevation × (10 / tower height)^0.2`, with towers 50 m (400 kV), 46 m (275 kV) and 27 m (132 kV) tall. Pylons on high ground see stronger wind and fall more often.
- **Failure:** each pylon has a lognormal fragility curve (50% chance of collapse within an hour at a 265 km/h gust at the pylon, β 0.12). Gust is sampled at 8 points along each route. One collapse takes the line out for the rest of the storm, so long, exposed lines are the vulnerable ones.
- **Consequence:** a DC power flow reroutes power after every collapse. Lines pushed past their rating trip, and are reclosed the next hour. Substations left in islands without enough generation are disconnected.

The app is a map of the UK with the grid on it, a storm picker and a **Simulate** button. Each run is one seeded storm (`POST /api/storm`) played back as a continuous animation. The storm glides across the map, lines fade to dashed red when a pylon falls (marked with a cross where it fell) or orange when they trip on overload, and substations losing supply turn red. Spans, fragility, ratings, dispatch and demand are illustrative, not utility data.

**Real storms vs. real outages.** Besides a synthetic storm, the picker replays eight named storms with hourly Met Office UKV gusts, scaled from the weather model's terrain to each route's real height: Bert (Nov 2024), Darragh (Dec 2024), Éowyn (Jan 2025), Amy (Oct 2025), Claudia (Nov 2025), Bram (Dec 2025), Goretti (Jan 2026) and Austen (Oct 2026). Real power cuts from `data/outages/unplanned_outages.csv` appear on the map as they started. A strip under the button compares the timing of real outage starts per hour with modelled pylon risk per hour, and a one-line summary compares place: each 0.25° area's peak gust against how far its outages rose above that area's normal daily rate. Outages come from the NGED feed (from May 2024) and the UKPN feed (from late October 2025), so only those licence areas have data; they are shaded on the map. They are distribution faults, not transmission pylons, so this tests where and when the risk lands, not how many things break. These are the named storms with a clear outage spike in the CSV; Ashley, Conall, Floris, Benjamin, Chandra and Dave barely register and are left out.

Rebuild the storms with `python scripts/build_storm_benchmark.py` (default CSV `data/outages/unplanned_outages.csv`; `--storm ID` for one). Weather comes from the Open-Meteo historical forecast API, cached per storm in `data/storms/.cache/` and paced to stay under its free-tier limit of 5,000 location-calls an hour, so a full rebuild takes about 90 minutes. The script writes `data/storms/<id>.json` and `data/storms/index.json`, which `GET /api/storms` lists.

The Monte Carlo reinforcement experiment, with mitigation planning and grid damage/repair logic, is in the backend (`POST /api/experiment`, IEEE 14-bus or GB) but has no UI.

## Great Britain network

`data/gb_network.json` is generated, not hand-written. To rebuild it from a checkout of the atlas:

```bash
python scripts/build_gb_network.py ../uk-grid-atlas
```

The script clusters OSM line endpoints within 2 km into substations, merges parallel ways into multi-circuit branches, contracts series chains and keeps the largest connected component (about 88% of the atlas's route length). Stations with a stated capacity are attached to the nearest substation. Reactances, ratings, dispatch and the 30 GW demand allocation are illustrative. Synthetic storms on this network are a depression crossing GB from south-west to north-east, so gusts vary by location. A GB experiment takes about 30 s with three worker processes; repeat settings are cached. Data © OpenStreetMap contributors (ODbL); see [ATTRIBUTION.md](ATTRIBUTION.md).

## Multi-day damage and recovery (API)

`POST /api/experiment` with `"mode": "multiday"` runs a 1–72 hour storm with probabilistic failures, weather-gated repairs by limited crews, and hourly reconnection. It accepts imported hourly weather JSON for historical exposure replay; `"mode": "legacy"` is the original five-hour experiment. See [multi-day inputs, recovery assumptions and data sources](docs/MULTIDAY.md). Imported weather does not supply real asset geography or calibrate failure probabilities.

## Mitigations (API)

The same endpoint compares reinforcement plans on shared storms: no action, two simple baselines, the greedy GridGuard selection, an optional `custom_lines` plan and, in multi-day mode, a `prepared` plan that reorders the repair queue. Each plan gets paired evidence and an illustrative cost/benefit from editable `economics` assumptions; `storm_type` picks one of three windstorm presets. On the GB grid, only the 24 most frequently failing lines are evaluated individually. See [decision assumptions](docs/DECISION_SUPPORT.md) and [map/ML integration and evaluation](docs/TEAM_INTEGRATION.md).

## Run the demo

Requires Python **3.11–3.14**. Python 3.13.5 was tested on Windows. The release ZIP includes the built frontend, so Node is optional for running it.

Run commands from the repository root (the directory containing `backend/`, `frontend/`, and `requirements.txt`). The release ZIP wraps this same layout in a single `gridguard/` directory.

In PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m backend.app
```

On macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m backend.app
```

For a source checkout, build the frontend using the **Edit and rebuild** steps below before opening the dashboard. Release ZIPs already include that build.

Open **http://127.0.0.1:5001**. Keep the terminal running; Ctrl+C stops it. Everything runs locally. Initial experiments take a few seconds; repeat settings are cached. The Flask development server binds to loopback and is intended for this local demo.

If your Python installation cannot bootstrap pip into a venv, install into it using the parent interpreter: `python -m pip --python .venv/Scripts/python.exe install -r requirements.txt` (replace the interpreter path on macOS/Linux).

## Edit and rebuild

Requires Node **20.19+ or 22.12+** (Node 24.14.0 tested). The React version follows upstream; Vite replaces Next's routing/build layer because this is one dashboard with a separate Flask computation service.

```powershell
cd frontend
npm ci
npm run build
cd ..
.venv\Scripts\python.exe -m backend.app
```

For hot reload, run Flask in one terminal and `cd frontend; npm run dev` in another. Open Vite's printed localhost URL. Its `/api` proxy targets Flask on port 5001. The production build is served directly by Flask, avoiding cross-origin configuration and a second runtime.

## A one-minute demo

1. Press **Simulate** with **Synthetic storm** selected. Each press uses a new seed, so the pylons that fall change from run to run.
2. Pick **Storm Darragh · Dec 2024** and press **Simulate**. Measured gusts sweep in from the south-west and about 2,100 real power cuts appear across NGED's areas. Darragh's gusts stay well below what topples a lattice tower, so few or no pylons fall.
3. Watch the strip under the button: yellow bars are real outage starts per hour, the blue line is modelled pylon risk per hour (each scaled to its own peak). The line below it gives the timing correlation and how much faster outages ran in the windiest areas than in the calmest.

If the backend is not running, the page replays a bundled synthetic storm (`data/sample_storm.json`) and says so; real storms need the backend.

## Monte Carlo experiment (API)

The reproducible result for the **original five-hour mode** (`POST /api/experiment` with defaults) is:

| Portfolio | Spend | Mean unserved energy (MWh) | Monte Carlo SE (MWh) | Major outage probability |
| --- | ---: | ---: | ---: | ---: |
| No action | 0 | 830.049 | 29.399 | 85.16% |
| Weather risk / cost | 5 | 674.800 | 34.397 | 64.84% |
| Intact power flow / cost | 5 | 497.222 | 29.266 | 47.66% |
| GridGuard marginal benefit / cost | 5 | 497.222 | 29.266 | 47.66% |

Read `data/sample_run.json` for authoritative unrounded-to-display results. GridGuard chooses **L04, L07, L01, L02**. It reduces estimated energy not served by about **40.1%** versus no action. The power-flow baseline selects the same set in this run and ties it. This is a synthetic demonstration, not evidence of real-world reliability improvement or general superiority. Different seeds may favor a baseline.

## How the Monte Carlo experiment works (IEEE 14-bus)

- **Network:** 14 benchmark buses, 259 MW total demand, 15 line branches and 5 transformer branches. IEEE bus numbers remain 1–14. `L01`–`L20` follow pandapower's exported branch order (transformers last), not a universal branch naming convention.
- **Active supply:** buses 1 and 2 have the original active dispatch (219 and 40 MW in the lossless intact case) and caps (332.4 and 140 MW). Buses 3, 6 and 8 are treated as synchronous condensers with no active generation. This is an explicit model choice; original case14 generator P limits at those zero-P buses are not enabled as new reserve plants.
- **Weather:** five one-hour exposure windows form a synthetic moving storm on schematic coordinates. Scenario severity is lognormal with log standard deviation 0.09 and mean one, shared across assets. A per-line exposure multiplier persists through the storm. Conditional trip draws are independent. These are artificial correlations, not a meteorological ensemble.
- **Illustrative fragility:** `p = clip(0.65 × sigmoid((gust_kmh − 90) / 12) × exposure × (1 + 0.002 × rain_mm_h), 0, 0.95)` per window. The peak-reference gust control scales a Gaussian spatial footprint and storm-window strength; local gusts differ from this reference.
- **Hardening:** 75% smaller weather-trip probability **and** 30% higher assumed thermal capacity. Hardening does not make a line invulnerable. All branches, including transformers, are eligible in this simplified model.
- **Power flow:** a lossless DC angle solve uses benchmark series reactance and transformer taps. Every island gets its own reference angle. Scheduled generation scales down for a surplus or draws on proportional headroom for a deficit; available capacity caps supply. Demand is shed proportionally within an undersupplied island. Shed loads remain disconnected through the event. The solver checks bus-level active-power conservation.
- **Cascades:** weather failures happen first in a window. Recompute DC flow, trip all surviving branches over rating together, recompute again, repeat until stable. Playback records the overloaded pre-trip flow and each recomputed state. Cascade rounds have zero duration in the energy accounting. Every round removes a branch, so termination is bounded by 20 removals.
- **Thermal ratings:** the case14 ratings are not useful here, so the demo assumes `max(22 MW, 1.55 × abs(intact DC flow) + 8 MW)`. These are active-power proxies, not original IEEE equipment ratings or MVA ratings. Branch costs repeat 1, 2, 3 budget units; exposures repeat deterministically between 0.75 and 1.25.
- **Monte Carlo:** 24 training storms select reinforcement. A separately seeded held-out bank of 16–512 storms (64–512 in the UI) evaluates all portfolios using common random numbers. Optimization never sees held-out outcomes. Seed controls both banks, with a fixed XOR domain separation for training.
- **Selection:** greedy positive marginal training mean energy-loss reduction divided by cost. It stops when nothing affordable improves the objective, so it may leave budget unused. It is a heuristic, not an exact knapsack/global optimum. Added edges surviving longer can redistribute flows, so benefit is not assumed monotonic.
- **Baselines:** no action; descending mean training fragility/cost; descending absolute intact power-flow/cost. The third is an electrical-flow baseline, not graph betweenness or an optimal dispatch.
- **Metrics:** energy not served sums settled MW shed over five one-hour windows; expected energy is the Monte Carlo mean. P95 is the empirical 95th percentile. Major outage means final load shed strictly exceeds 50% of original demand. SE is the sample standard deviation divided by √N. Paired SE is computed on per-storm before-minus-after differences. These quantify simulation sampling noise only.

## Verification

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
# Re-export the real benchmark and pandapower reference flows:
.venv\Scripts\python.exe scripts/export_benchmark.py
# Recreate the example JSON and the offline sample storm:
.venv\Scripts\python.exe scripts/sample_run.py
.venv\Scripts\python.exe scripts/sample_storm.py
cd frontend
npm run build
```

The test suite checks benchmark identity, full-service intact dispatch, bus-level conservation, agreement with pandapower's intact DC flows, generator-free islands, generation-limited island shedding, actual overload cascade trips, bounded termination, monotonic disconnected load, energy integration, reproducibility, feasible budgets and API validation. See `docs/VALIDATION.md` for completed checks and browser evidence.

## Extension points and limits

`backend/weather.py:load_profile()` is the weather-provider hook; `data/sample_weather.json` is the documented input fixture. `docs/INTEGRATIONS.md` describes forecast ensembles, required geographic asset mapping and next-step **GDELT** event context. No external requests happen at app runtime.

This prototype excludes AC voltage/reactive limits, transient/frequency stability, realistic relay delays, optimal redispatch, repairs outside multi-day mode, critical-load priorities and validated storm correlations. The five-window horizon, fragility and ratings are intentionally demo assumptions. It must not drive operational grid decisions.

## Attribution and layout

The upstream [IC Hack 2026 WildFire repository](https://github.com/ltanak/ICHack26) was inspected at **dac1afe03655081b6633ba3eeb07f45db86834aa**, including its MIT licence, Flask API, simulation loop and React simulation/mitigation components. The retained architecture and workflow are described in `ATTRIBUTION.md`; original copyright and licence text are included. Wildfire spread code, satellite assets, pretrained models, credentials and large datasets are not part of this deliverable.

```text
backend/                 Flask API, DC cascade simulator, storm engine, real-storm benchmark, mitigations
frontend/src/            React map: UK outline, grid, storm picker, continuous playback, outage comparison
frontend/dist/           Built browser app (included in release ZIP)
data/ieee14.json          Pandapower benchmark export + DC reference flows
data/gb_network.json      GB 132–400 kV grid from OSM, with terrain elevation
data/outages/             Real unplanned power cuts (CSV) used by the storm comparison
data/storms/              Real storms: hourly gusts + outages per storm, and index.json
data/sample_storm.json    Offline sample storm for the frontend
data/sample_weather.json  Synthetic weather fixture
data/sample_run.json      Reproducible example output
scripts/                 Build the GB grid and storm benchmarks; re-export benchmark and samples
tests/                   Meaningful physics/API checks
third_party/             Retained licences
docs/                    Architecture, integrations, validation, screenshots
```
