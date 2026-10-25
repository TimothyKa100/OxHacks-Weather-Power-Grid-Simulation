# GridGuard

A geographic grid stress-test and mitigation tool. It combines engineering fragility
with bounded relative risk from the saved XGBoost model, then simulates line
failures, DC cascades, repairs and investment decisions.

[Model specification](docs/MODEL_SPEC.md) · [Measured integration results](docs/HYBRID_RESULTS.md) · [Target/feature contract](config/transmission_model_contract.json)

- **Map:** 478 substation buses (one per voltage level at a site) and 673 branches at 132–400 kV: 436 overhead routes from uk-grid-atlas (OSM), 51 underground 275/400 kV cables from the grid archive, 137 transformers and 49 inferred links. Every single-circuit outage but one leaves supply intact. Electrical parameters, dispatch and demand allocation remain illustrative.
- **Weather:** synthetic geographic windstorms (single peak, persistent winds or two peaks), the tracked Storm Darragh historical replay, other locally built storms, or imported hourly GB weather / model probabilities.
- **One decision engine:** the map uses `POST /api/storm-plan`. Route length, sampled terrain and pylon fragility supply illustrative hourly tower-damage probabilities. The GB storm engine separates tower damage (crews, days), transient faults (auto-reclose or remote reclose) and overload trips. It re-dispatches before and after faults, trips lines above a short-term rating, follows winter demand and wind cut-out, and collapses islands without synchronous generation. See [the cascade model](docs/CASCADE_MODEL.md). No precise pylon-collapse location is inferred by this planner.
- **Two audiences:** public outage preparedness and organisation/government mitigation. Choose suggested or custom line reinforcements, compare repair priorities, and apply a plan to the same completed storm and seed.
- **Evidence and costs:** paired held-out loss differences, peak/final major outages, worst-decile loss, recovery, editable asset investment costs, benefit/cost ratios and break-even events. Costs and interruption values are assumptions.

Run a comparison, select a portfolio in the decision table to show it on the map, and pause/scrub playback. Green routes are reinforced; red routes have tower damage; orange routes tripped on overload; yellow circuits are locked out after a failed auto-reclose; faded routes have a circuit out. Cables are dashed and inferred links dotted. Observed outage dots and the historical wind-risk chart remain fixed when hypothetical interventions change the simulation. Export includes full cascade frames and decision assumptions.

## What the model means

The intended target is a new weather-caused transmission-branch outage in the next
hour, conditional on operating at interval start. The pending dataset is assumed to
supply the declared features and target; it is not in this checkout. No empirical
transmission-failure training result is claimed.

The existing saved XGBoost model predicts distribution fault reports within 5 km of
a line route. The UI defaults to **Engineering + relative ML risk**: a bounded risk
redistribution among comparable lines, preserving the engineering expected number
of line failures each hour. It runs without new data, retraining or an uploaded
reference model. Cross-target use remains an assumption, not calibrated transmission
reliability. Purple halos still show the raw area-risk scores as context.

## Calculation

1. Engineering route exposure produces a whole-line hourly probability `p_eng`.
2. Use complete saved Darragh scores when hours match; otherwise run the saved model
   on available raw route features. Unknown weather stays missing, rather than zero.
   Synthetic partial-feature inference is explicitly unvalidated.
3. Compare each score with a leave-one-out median score from
   at least 5 and up to 12 peers: gust within 10 km/h and engineering hazard within
   a factor of 4. Outside that support, retain engineering risk. Transfer beyond
   the SSEN training region is an explicit, unvalidated assumption.
4. Compute `s = logit(q_full) - logit(q_reference)` and
   `m = clip(exp(beta*s), min_multiplier, max_multiplier)`.
5. Rescale supported multipliers within the bounds so that
   `sum(1 - (1-p_eng)^m) = sum(p_eng)` each hour. Beta zero reproduces engineering mode.
6. Apply the assumed reinforcement hazard multiplier, then sample failures using
   shared seeded random draws.
7. Find islands, balance generation, solve DC power flows, trip overloaded branches
   simultaneously and repeat until stable. Repair crews and reconnection operate
   at hourly boundaries.
8. Mark nodes as supplied, reduced supply or out of supply; zero-load nodes can
   become deenergized. Compare energy loss, node outages, tail risk, recovery and costs.

`data/gb_network.json` is generated, not hand-written. To rebuild it from a checkout of the atlas:

```bash
python scripts/build_gb_network.py data/grid/raw/uk_grid_atlas data/grid/uk_power_grid.sqlite
python scripts/security_analysis.py
```

The builder clusters endpoints of the same voltage within 2 km into substation buses and splits routes at T-joins the clustering missed. Separate routes stay separate branches; overhead routes are assumed double-circuit. It adds the archive's 275/400 kV underground cables, transformers between co-located voltage levels, links from dead ends to a substation of the same voltage within 10 km, interconnector imports and boundary supplies where coverage ends. It keeps the largest connected component (96% of overhead route length) and sizes transformer and cable counts the data lacks to secured operating points. `security_analysis.py` reports the single-circuit (N-1) result. Reactances, ratings, dispatch and the 30 GW demand allocation are illustrative; see [the cascade model](docs/CASCADE_MODEL.md). Synthetic storms on this network are a depression crossing GB from south-west to north-east, so gusts vary by location. A GB storm plan with 16 held-out scenarios takes about 100 s with three worker processes; repeat settings are cached. Data © OpenStreetMap contributors (ODbL); see [ATTRIBUTION.md](ATTRIBUTION.md).

## Grid-archive assets on the map

The map also draws the grid archive's 887,683 located assets: 240k substations, 9.6k power stations, 625k embedded generators, 11.8k transformers, plus storage, HVDC converters, compensators and switchgear. Build the index once with `python scripts/build_asset_index.py`; it writes `data/grid/asset_index.parquet` (git-ignored).

There are no preset zoom levels. Each asset has a continuous importance score from its kind, voltage and capacity. For whatever is on screen, `GET /api/assets` returns the ~1,000 most important assets in view, spread across the view (`backend/assets.py`). Zooming in shrinks the view, so smaller stations, substations and transformers rise into the budget.

The cascade still runs on the transmission backbone; hundreds of thousands of assets without electrical parameters cannot be solved hourly. Each asset is linked to its nearest backbone bus, its stand-in supply point, and turns red when the simulation leaves that bus short of supply. Northern Ireland assets are shown but not simulated.

## Multi-day damage and recovery

`POST /api/experiment` with `"mode": "multiday"` runs a 1–72 hour storm with probabilistic failures, weather-gated repairs by limited crews, and hourly reconnection. It accepts imported hourly weather JSON for historical exposure replay; `"mode": "legacy"` is the original five-hour experiment. See [multi-day inputs, recovery assumptions and data sources](docs/MULTIDAY.md). Imported weather does not supply real asset geography or calibrate failure probabilities.

## Mitigations

The same endpoint compares reinforcement plans on shared storms: no action, two simple baselines, the greedy GridGuard selection, an optional `custom_lines` plan and, in multi-day mode, a `prepared` plan that reorders the repair queue. Each plan gets paired evidence and an illustrative cost/benefit from editable `economics` assumptions; `storm_type` picks one of three windstorm presets. On the GB grid, only the 24 most frequently failing lines are evaluated individually. See [decision assumptions](docs/DECISION_SUPPORT.md) and [map/ML integration and evaluation](docs/TEAM_INTEGRATION.md).

## One storm chain

`POST /api/storm-plan`, the **Simulate & compare** button and `python scripts/run_chain.py` run
one chain with two outputs from the same weather.

- **Local power-cut forecast (ML).** Corridor model `gb-corridor-xgb-v2` gives, for each
  overhead route and hour, the chance of a reported distribution cut within 5 km
  (`backend/forecast.py`).
  - On the map it shows as purple halos (**ML power-cut forecast (this run)**), next to the real
    reported cuts (yellow dots). Hovering a line shows its forecast and the reports near it.
  - For historical storms, the panel charts forecast and reported corridor-hours on the same
    scale, with totals, timing and ranking.
- **Transmission grid stress (physics).**
  - The chain runs the engineering route/pylon hazard, line failures, the cascade and every
    mitigation on matched draws, including reinforcement plus prioritized repairs.
  - It then runs post-storm restoration, cost-benefit and a recommendation.
  - Playback slows down for each overload cascade, zooms to the tripped lines, steps through
    the protection rounds and then returns. **Cascades in this playback** jumps to each one.

The ML does not set line failures. `failure_mode: "hybrid"` remains an experimental option that
lets it shift failure probability between comparable lines. The decision panel's **Storm chain**
card summarises each stage. See [HYBRID_RESULTS.md](docs/HYBRID_RESULTS.md).

## Demo data

`demo_data/` holds only what the simulation and demo read: networks, storm replays, the detail
graph and asset index, the v1/v2 corridor models and archived replay predictions (about 27 MB).
The backend reads `demo_data/` when it has been built, otherwise `data/`; set `GRIDGUARD_DATA`
to choose (see `backend/paths.py`). The full `data/` tree (~18 GB) is only needed to rebuild
datasets or retrain. Rebuild the demo folder after retraining with
`python scripts/build_demo_data.py`. `--check` reports when it differs from `data/`.

## Failure-probability model (XGBoost)

`failure_model/` trains a calibrated XGBoost model of hourly weather-caused outage risk in each GB line's 5 km corridor. It uses SSEN's 2015–2026 fault register and ERA5 route weather (fetched on demand for any GB window), with the first 75% of hours for development and the last 25% for testing. `python -m failure_model gb-export` writes a layer that the map shows as purple corridor-risk halos (**Corridor outage risk** picker), alongside the engineering line model. The probabilities are **area-level outage risk, not verified transmission-line failures**, so the simulator refuses them as line-failure inputs. See [GB_LINE_MODEL.md](docs/GB_LINE_MODEL.md). The data comes from the `dataRefininf` archive, extracted into `data/` (git-ignored); install `requirements-model.txt`.

## Reliable web preview

From the repository root, install frontend dependencies once (`cd frontend`, `npm ci`, then `cd ..`). With Python requirements already installed, start the built frontend and API together:
Conservation concerns each hour's hypothetical operating-line probabilities before
reinforcement. Actual failures, cascades and recovery can differ because different
lines break. Node outages follow the electrical solution, with no separate sampled
physical substation-damage model. The reference is a peer statistic, not a newly
fitted ML model. Matching reduces obvious double counting; it does not prove causality.

Median pylon failure gust defaults to 265 km/h, dispersion to 0.12, beta to 0.5,
and multiplier bounds to [0.5,2]. These are editable assumptions. Weather remains
fixed while fragility or hybrid settings change. No probability threshold forces
an asset to fail. Full per-asset probability/draw diagnostics are exported.

## Run the app

Python 3.11–3.14 and Node 20.19+ or 22.12+:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci --prefix frontend
.venv/bin/python scripts/serve_preview.py
```

On Windows use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.
Open http://127.0.0.1:5001 and keep the terminal running. This serves UI and API
on one port. For a hosted development preview, add `--host 0.0.0.0 --port 5173`.
For separate hot reload, run Flask on 5001 and `npm run dev --prefix frontend`.

To try it, press **Simulate & compare**. Defaults are an explicitly synthetic stress
test: seed **7**, **12 hours**, **190 km/h**; the fragility curve remains unchanged.
Select a branch and open the asset inspector to see the
engineering/full/reference probabilities, multiplier, applied probability and draw.
Apply a suggested/custom plan to rerun that completed event unchanged.

Optional uploaded pairs still use the earlier unnormalised residual experiment;
they must cover every GB branch and match its hours/provenance. Clear the pair with
**Use built-in model** to return to the conservative default. The old
`data/examples/synthetic_hybrid_pair.json` remains a synthetic integration fixture.
Raw corridor probabilities are never sampled as physical failure probabilities.

## Train with the pending feature/label table

```bash
.venv/bin/python -m pip install -r requirements-model.txt
.venv/bin/python -m failure_model.paired train rows.parquet config/transmission_model_contract.json data/failure_model/models/transmission-pair-v1
.venv/bin/python -m failure_model.paired export inference_features.parquet data/failure_model/models/transmission-pair-v1 paired.json
```

These commands require the unpublished rows; they do not invent labels. The contract
is marked `assumed_target`. Observed transmission training additionally requires
verified asset identity, verified labels and operating/reporting exposure. Training,
tuning, calibration and final testing are chronological and separated by storm/group
and time gaps. Immutable versions save both models, preprocessing, calibration,
checksums and held-out metrics. See the specification for columns and evidence gates.

## Network, data and limits

The simulated GB backbone has **303 buses and 367 branches**, with OSM routes and
terrain. It represents approximately 33,183 pylons using 5,615 route exposure samples.
Reactance, ratings, generation dispatch, demand allocation and repair times remain
illustrative. IEEE14 remains available for solver regression tests.

The separately documented ~4.12M-record equipment catalogue and full source/training
archives are not bundled and are not loaded by this electrical solver. Source-scoped
equipment records are not automatically electrical buses or verified terminal links.

The tracked Darragh replay retains historical weather and distribution outage dots.
Observations do not change when hypothetical reinforcement is applied. Weather
reanalysis is not an archived-forecast backtest. There is no AC voltage/frequency
model, operational protection timing, real crew routing or learned hardening effect.
Public preparedness guidance has no numerical effect on asset failure rates.

## Verification and results

```bash
.venv/bin/python -m pip install -r requirements-dev.txt -r requirements-model.txt
.venv/bin/python -m pytest -q
npm test --prefix frontend
npm run build --prefix frontend
.venv/bin/python scripts/benchmark_hybrid.py
```

The saved sensitivity experiment covers 27 synthetic-score cases on the actual GB
prototype. It verifies coupling and assumptions, not predictive accuracy. It includes
paired uncertainty, beta-zero equality, fragility sensitivity and a full mitigation
example. Training/export tests use synthetic labels and are labelled accordingly.

Source and licence details remain in [ATTRIBUTION.md](ATTRIBUTION.md) and
`third_party/`. Grid geometry © OpenStreetMap contributors, ODbL, via
[uk-grid-atlas](https://github.com/jacobwright32/uk-grid-atlas). Retain attribution.
