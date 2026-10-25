# GridGuard model specification

This is the current canonical description. It distinguishes implemented calculations,
results that can be reproduced with the repository, and the declared contract for
unpublished data. Older documents describe earlier versions and experiments.

## Target and available data

The intended physical target is, for electrical branch i and interval [t,t+1h):

**P(new weather-caused transmission-branch outage during the next hour, conditional
on operating at the interval start and information available at the prediction cutoff).**

This includes a specified direct weather outage mode, not a downstream supply loss
or an overload trip already produced by the cascade solver. Label those mechanisms
separately to prevent counting the same propagation twice.

`config/transmission_model_contract.json` assumes the feature columns and intended
target of the pending data. Its `assumed_target` marker does not assert that real
labels have been obtained. No new transmission model is claimed to have been fitted
on unpublished data. Change to `observed_labels` only after verifying asset identity,
label classification and operating/reporting exposure.

What is actually present:

- The GB simulation: 303 aggregate buses, 367 branches, approximate OSM topology,
  estimated route pylons/terrain, assumed reactance, ratings, generation and demand.
- Saved `gb-corridor-xgb-v1` weights/calibration and a Darragh prediction export.
  Its target is distribution fault reports inside a 5 km corridor, not transmission
  asset failure. The raw export remains display-only.
- A larger ~4.12M-record catalogue is documented on `dataRefininf` but its databases,
  normalized tables and full training partitions are not in this checkout. Equipment
  records are not automatically electrical buses. This change does not load that
  catalogue or reconstruct its operational electrical network.

## Feature and label table

One row per asset and UTC hour, with `asset_id`, `timestamp_utc`, `storm_id`, `split`
(fit/tune/calibrate/test), `failed_next_hour` (0/1), and the ordered feature columns:

| Feature | Convention |
|---|---|
| gust_max_kmh | Route maximum gust for the target hourly interval |
| gust_p95_kmh | Route-length-weighted p95 gust |
| gust_prev_3h_max_kmh | Maximum route gust during the preceding 3 h |
| crosswind_max_kmh | Route-segment gust × absolute sine of wind/segment angle |
| rain_1h_mm | Route-weighted precipitation during the interval |
| rain_prev_24h_mm | Route-weighted total preceding 24 h |
| temperature_c | Consistent interval temperature convention |
| length_km | Mapped route length |
| ele_mean_m, ele_max_m | Route terrain elevation summaries |
| kv, circuits | Voltage class and represented circuit count |

The current corridor model uses raw ERA5 weather, not the engineering altitude-adjusted
gust. Preserve consistent feature units and weather conventions in fitting/inference.
The separately calculated engineering prior uses its documented local gust convention.

Transmission rows require `operational_at_start=true` and `reporting_complete=true`.
For `observed_labels`, also require `asset_identity_verified=true` and
`label_verified=true`. Exclude ongoing outages, uncertain reporting, planned work and
ambiguous identity. Absent incident reports are not sufficient negative evidence.
Optional vegetation/age/condition/maintenance predictors may be declared only when
real columns exist. Missing values stay missing for XGBoost; the logistic reference
uses imputation and scaling fitted exclusively on its fitting partition.

For historical replay, interval weather can be reanalysis. This is not a prospective
forecast backtest. Genuine forecasts need archived issue/valid/availability times;
metadata alone cannot verify that feature engineering excluded hindsight.

## Paired learning

`python -m failure_model.paired train ROWS.parquet CONTRACT.json MODEL_DIRECTORY`
fits a full XGBoost classifier and a portable weather-aware logistic reference for
the **same declared target**. Training uses every supplied row without class weights
or synthetic oversampling. Data partitions must be chronological, separated by at
least 25 h (24 h lookback + horizon), and have disjoint storm/group IDs. Tune chooses
the early-stopping point. Separate calibration fits a sigmoid on each model margin.
A reversed calibration slope is rejected for investigation.

The reference uses gust maximum/p95/history, crosswind, route length/elevation,
voltage and circuit count. These deliberately overlap the engineering exposure
inputs. It helps reduce obvious double counting of windy conditions; because a
linear reference and nonlinear full model differ, the residual is not guaranteed
to isolate independent causes or a causal intervention effect.

Each immutable model directory contains full model weights, calibration,
reference coefficients/preprocessing/calibration, manifest checksums and held-out
Brier/log-loss/precision-recall metrics. Synthetic or assumed-target scores retain
that evidence marker and are not real-world validation. Target calibration does
not by itself calibrate the final hybrid transmission hazard.

The existing GB corridor training command now also saves a calibrated reference.
`gb-export --paired` exports it when present. Existing v1 has no saved reference;
it cannot produce a pair without retraining to a new model directory. No constant
historical rate or guessed reference is silently substituted. Canonical JSON writes
use LF; legacy CRLF calibration checksums are accepted only when an exact newline
variant matches the recorded digest.

## Engineering prior and hybrid probability

At every route sample, local gust comes from synthetic/historical exposure and the
engineering terrain/tower-height adjustment. The illustrative pylon curve is:

```
p_pylon = NormalCDF(log(local_gust / median_gust) / dispersion)
p_engineering_line = 1 - product((1 - p_pylon_sample) ** represented_pylons)
```

Median gust defaults to 265 km/h and dispersion to 0.12. Both are editable scenario
parameters, not empirically established GB failure thresholds. Changing them keeps
the seeded weather field fixed: weather RNG is independent of failure draws.

Engineering mode uses that line probability. Hybrid mode requires an explicit pair
with full score q_full and reference q_ref for every branch and every matching hour:

```
s = logit(q_full) - logit(q_ref)
m = clip(exp(beta * s), min_multiplier, max_multiplier)
lambda_engineering = -log(1 - p_engineering) / dt
p_hybrid = 1 - exp(-lambda_engineering * m * dt)
```

Numerical logits are clipped at 1e-12. Computation uses stable log/exponential
operations; engineering p=0 stays zero and p=1 stays one. Default beta=.5 and bounds
[.5,2] are **assumptions**. API bounds: beta 0–2, min .05–1, max 1–10. Beta=0 or equal
full/reference scores reproduces engineering probability. A high ML score does not
force damage or introduce a probability floor. Hybrid is not the sum or product of
the two raw probabilities. A corridor-target pair remains an explicitly experimental
cross-target correction; a transmission-target pair still needs beta validation.

No transmission observations are available here to fit beta, fragility parameters
or multiplier bounds. Future fitting must use a reserved development dataset with
verified transmission outcomes; freeze all these choices before final test reporting.

## Failures, reinforcement, cascades and repair

After combining hazards, reinforcement applies an assumed 0.25 hazard multiplier:
`p_applied = 1 - (1-p_hybrid)^0.25`. It separately raises the thermal rating by 30%.
Sample an operating branch's weather failure when its shared uniform draw is below
p_applied. Every playback frame exports engineering/full/reference probabilities,
multiplier, unreinforced/applied probabilities, draw, eligibility and sampled outcome.

The same DC solver then finds islands, balances generation within capacity, sheds
insufficient supply proportionally, solves flows, trips overloaded lines together,
and repeats to a stable state. Rounds have zero elapsed duration. Thermal ratings
and dispatch remain illustrative. No ML probability represents an overload already
computed by this engine.

At hourly boundaries, crews repair a deterministic or training-prioritized queue.
Local gust ≥60 km/h pauses work. Weather repairs have seeded lognormal durations
(median 8 h, clipped 2–36); overload inspection/reset takes 2 crew-hours. Demand
reconnection is attempted hourly. Travel, protection delays, AC voltage/frequency,
actual switching and component-level repair logistics are not modelled.

## Decisions, comparisons and interpretation

Loss integrates settled shed MW × timestep hours. Compare no action, weather-risk,
flow-risk, greedy reinforcement, custom plans and repair priority. Selection uses
24 simulation training draws and an independent evaluation draw bank. On GB, only
24 high-frequency failure candidates are evaluated individually. This is a heuristic.

Hybrid mode also runs engineering mode with the same weather, seed, repair draws,
budget and horizon. The `hazard_comparison` includes no action and, if supplied, an
identical custom portfolio. These isolate the hazard change. Optimized portfolios
are selected independently under each hazard model; comparing them also changes
the investment decision. Repeated manual tuning on one bank is exploratory.

Report mean/tail unserved energy, peak/final major-outage probability, recovery,
and paired mean differences with simulation confidence intervals. These intervals
exclude model, target-transfer, weather-forecast and financial uncertainty. Costs
are editable assumptions; net benefit is avoided MWh × outage value × equivalent
events minus investment. Public preparedness advice has no numerical effect on
line fragility.

Raw corridor overlays remain observational context. Historical outage dots remain
unchanged by hypothetical interventions. The simulator produces named hypothetical
line failures; it does not identify which real transmission assets broke.

## Run and reproduce

```
python -m pip install -r requirements-model.txt -r requirements-dev.txt
python -m failure_model.paired train rows.parquet config/transmission_model_contract.json data/failure_model/models/transmission-pair-v1
python -m failure_model.paired export inference_features.parquet data/failure_model/models/transmission-pair-v1 paired.json
python scripts/benchmark_hybrid.py --output docs/results/hybrid_sensitivity.json
python scripts/serve_preview.py
```

The first two training/export commands require the pending feature/label tables;
they cannot reconstruct them from the saved corridor model. Import paired.json,
choose its matching storm and select Experimental hybrid in the UI. Matching branch
order, complete hourly coverage, provenance and fitting cutoff are enforced.
See [HYBRID_RESULTS.md](HYBRID_RESULTS.md) for measured integration results.
