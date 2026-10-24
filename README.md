# GridGuard

A storm crossing the Great Britain transmission grid, one hour at a time: wind → pylons → lines → power flow.

- **Grid:** 303 substations and 367 lines at 132–400 kV, built from [uk-grid-atlas](https://github.com/jacobwright32/uk-grid-atlas) OpenStreetMap data.
- **Pylons:** each line's pylon count is its route length ÷ a typical span (400 kV 360 m, 275 kV 350 m, 132 kV 280 m), about 33,000 in total.
- **Elevation:** every substation and a point every ~2 km along each real OSM route carry terrain height from EU-DEM (25 m). Local gust is scaled by the UK National Annex to BS EN 1991-1-4 altitude factor, `c_alt = 1 + 0.001 × elevation × (10 / tower height)^0.2`, with towers 50 m (400 kV), 46 m (275 kV) and 27 m (132 kV) tall. Pylons on high ground see stronger wind and fall more often.
- **Failure:** each pylon has a lognormal fragility curve (50% chance of collapse within an hour at a 265 km/h gust at the pylon, β 0.12). Gust is sampled at 8 points along each route. One collapse takes the line out for the rest of the storm, so long, exposed lines are the vulnerable ones.
- **Consequence:** a DC power flow reroutes power after every collapse. Lines pushed past their rating trip, and are reclosed the next hour. Substations left in islands without enough generation are disconnected.

The dashboard runs one seeded storm (`POST /api/storm` with `peak_gust`, `hours`, `seed`) and plays it back on the map. Spans, fragility, ratings, dispatch and demand are illustrative, not utility data.

**Real storm benchmark.** The "Real: Storm Darragh" tab replays 6–8 December 2024 with hourly Met Office UKV gusts, scaled from the weather model's terrain to each route's real height. It overlays the 2,101 real NGED power cuts that started in those 60 hours, and compares two things. Timing: modelled pylon risk per hour against real outage starts per hour. Place: each 0.25° area's peak gust against how far its outages rose above the area's normal daily rate. Those outages are distribution faults, not transmission pylons, so this tests where and when the risk lands, not how many things break. Rebuild with `python scripts/build_storm_benchmark.py /path/to/unplanned_outages.csv`.

The older Monte Carlo / reinforcement experiment (`POST /api/experiment`, IEEE 14-bus or GB) is still in the backend but has no UI.

## Great Britain network

`data/gb_network.json` is generated, not hand-written. To rebuild it from a checkout of the atlas:

```bash
python scripts/build_gb_network.py ../uk-grid-atlas
```

The script clusters OSM line endpoints within 2 km into substations, merges parallel ways into multi-circuit branches, contracts series chains and keeps the largest connected component (about 88% of the atlas's route length). Stations with a stated capacity are attached to the nearest substation. Reactances, ratings, dispatch and the 30 GW demand allocation are illustrative. Synthetic storms on this network are a depression crossing GB from south-west to north-east, so gusts vary by location. A GB experiment takes about 30 s with three worker processes; repeat settings are cached. Data © OpenStreetMap contributors (ODbL); see [ATTRIBUTION.md](ATTRIBUTION.md).

## Multi-day prototype

The dashboard now defaults to a 48-hour synthetic storm with probabilistic failures and limited repair crews. Import hourly weather JSON for historical exposure replay, or select the original five-hour demo. See [multi-day inputs, recovery assumptions and data sources](docs/MULTIDAY.md). Imported weather does not supply real asset geography or calibrate failure probabilities.

## Decision support prototype

Residents receive general preparedness guidance. Organisations can compare repair priorities, choose suggested or custom reinforcements, and rerun the same storm. Editable financial assumptions show avoided-loss/cost ratios and break-even event counts. Three synthetic windstorm presets support stress tests. See [decision assumptions](docs/DECISION_SUPPORT.md) and [map/ML integration and evaluation](docs/TEAM_INTEGRATION.md).

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

## A 90-second demo

1. Select **Original 5-hour demo** and use **100 km/h, seed 26, 128 scenarios, 5 budget units**. Click **Run experiment** if you changed anything.
2. Look at the four summary cards, then choose **No action** and move the hour slider from intact through the storm. Dashed pink branches failed in weather; orange branches tripped in a cascade.
3. Click **Reinforced** or **Compare**. Green branches are the chosen investments. Click a line to inspect power, loading, local gust and Monte Carlo trip frequency; click a bus to inspect generation, shed load and island membership.
4. Use **Play cascade** for hourly playback. Within-hour event buttons expose the weather hit and subsequent simultaneous overload trip batches individually. Playback uses the same scenario for all portfolios: the median unmitigated held-out loss, selected without inspecting intervention performance.
5. Switch between **GridGuard**, **weather risk**, and **power-flow** selection. The comparison table always retains all four portfolios, including no action.
6. Reduce the budget to zero, change intensity or seed, and rerun. A banner marks changed controls so old results cannot be mistaken for a new run. **Export run** downloads the complete JSON, including settings, portfolios, uncertainty estimates and playback frames.

The reproducible result for the **original five-hour mode** is:

| Portfolio | Spend | Mean unserved energy (MWh) | Monte Carlo SE (MWh) | Major outage probability |
| --- | ---: | ---: | ---: | ---: |
| No action | 0 | 830.049 | 29.399 | 85.16% |
| Weather risk / cost | 5 | 674.800 | 34.397 | 64.84% |
| Intact power flow / cost | 5 | 497.222 | 29.266 | 47.66% |
| GridGuard marginal benefit / cost | 5 | 497.222 | 29.266 | 47.66% |

Read `data/sample_run.json` for authoritative unrounded-to-display results. GridGuard chooses **L04, L07, L01, L02**. It reduces estimated energy not served by about **40.1%** versus no action. The power-flow baseline selects the same set in this run and ties it. This is a synthetic demonstration, not evidence of real-world reliability improvement or general superiority. Different seeds may favor a baseline.

## How the model works

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
# Recreate the example JSON:
.venv\Scripts\python.exe scripts/sample_run.py
cd frontend
npm run build
```

The test suite checks benchmark identity, full-service intact dispatch, bus-level conservation, agreement with pandapower's intact DC flows, generator-free islands, generation-limited island shedding, actual overload cascade trips, bounded termination, monotonic disconnected load, energy integration, reproducibility, feasible budgets and API validation. See `docs/VALIDATION.md` for completed checks and browser evidence.

## Extension points and limits

`backend/weather.py:load_profile()` is the weather-provider hook; `data/sample_weather.json` is the documented input fixture. `docs/INTEGRATIONS.md` describes forecast ensembles, required geographic asset mapping and next-step **GDELT** event context. No external requests happen at app runtime.

This prototype excludes AC voltage/reactive limits, transient/frequency stability, realistic relay delays, optimal redispatch, repairs, critical-load priorities and validated storm correlations. The five-window horizon, fragility and ratings are intentionally demo assumptions. It must not drive operational grid decisions.

## Attribution and layout

The upstream [IC Hack 2026 WildFire repository](https://github.com/ltanak/ICHack26) was inspected at **dac1afe03655081b6633ba3eeb07f45db86834aa**, including its MIT licence, Flask API, simulation loop and React simulation/mitigation components. The retained architecture and workflow are described in `ATTRIBUTION.md`; original copyright and licence text are included. Wildfire spread code, satellite assets, pretrained models, credentials and large datasets are not part of this deliverable.

```text
backend/                 Flask API, DC cascade simulator, weather adapter
frontend/src/            React dashboard, SVG network, playback and comparisons
frontend/dist/           Built browser app (included in release ZIP)
data/ieee14.json          Pandapower benchmark export + DC reference flows
data/sample_weather.json  Synthetic weather fixture
data/sample_run.json      Reproducible example output
scripts/                 Re-export benchmark and sample output
tests/                   Meaningful physics/API checks
third_party/             Retained licences
docs/                    Architecture, integrations, validation, screenshots
```
