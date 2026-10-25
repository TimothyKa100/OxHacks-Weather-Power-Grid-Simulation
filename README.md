# GridGuard

A geographic grid stress-test and mitigation tool. It combines engineering fragility
with bounded relative risk from the saved XGBoost model, then simulates line
failures, DC cascades, repairs and investment decisions.

[Model specification](docs/MODEL_SPEC.md) · [Measured integration results](docs/HYBRID_RESULTS.md) · [Target/feature contract](config/transmission_model_contract.json)

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
