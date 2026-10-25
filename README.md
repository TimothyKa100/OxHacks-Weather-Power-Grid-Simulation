# GridGuard

A geographic grid stress-test and mitigation tool. It combines engineering fragility
with an optional, bounded correction from paired ML predictions, then simulates line
failures, DC cascades, repairs and investment decisions.

[Model specification](docs/MODEL_SPEC.md) · [Measured integration results](docs/HYBRID_RESULTS.md) · [Target/feature contract](config/transmission_model_contract.json)

## What the model means

The intended target is a new weather-caused transmission-branch outage in the next
hour, conditional on operating at interval start. The pending dataset is assumed to
supply the declared features and target; it is not in this checkout. No empirical
transmission-failure training result is claimed.

The existing saved XGBoost model predicts distribution fault reports within 5 km of
a line route. Those raw probabilities remain a separate purple map layer. An explicit
**Experimental hybrid** mode can use full/reference prediction pairs as a bounded
hazard correction. Cross-target use remains a scenario assumption, not calibrated
transmission reliability. The old model has no fitted reference artifact; it cannot
silently substitute a constant rate to create one.

## Calculation

1. Engineering route exposure produces a whole-line hourly probability `p_eng`.
2. A full ML model and a weather-aware reference predict the same declared target.
3. Compute `s = logit(q_full) - logit(q_reference)` and
   `m = clip(exp(beta*s), min_multiplier, max_multiplier)`.
4. Set `p_hybrid = 1 - (1-p_eng)^m`. Beta zero reproduces engineering mode.
5. Apply the assumed reinforcement hazard multiplier, then sample failures using
   shared seeded random draws.
6. Find islands, balance generation, solve DC power flows, trip overloaded branches
   simultaneously and repeat until stable. Repair crews and reconnection operate
   at hourly boundaries.
7. Compare energy loss, tail risk, recovery and illustrative financial outcomes.

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

To try the hybrid integration without claiming fitted ML: choose Synthetic storm,
seed **7**, **12 hours**, **190 km/h**, import
`data/examples/synthetic_hybrid_pair.json`, and run the comparison. The file is
explicitly synthetic. Select a branch and open the asset inspector to see the
engineering/full/reference probabilities, multiplier, applied probability and draw.
Apply a suggested/custom plan to rerun that completed event unchanged.

A real pair must cover every GB branch in exact API order and exactly match the
storm's hourly timestamps. Missing reference scores or mismatched provenance are
rejected. Raw corridor-risk files cannot be used as physical failure probabilities.

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
