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
  asset failure. Its raw probability is a display layer; the built-in hybrid uses
  only a bounded relative signal derived from it.
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

## Default approach with no new data

The UI defaults to **engineering rate + relative corridor ML risk**, implemented in
`backend/ml_risk.py` and `backend/hybrid.py`. There is no fitted transmission target,
so letting corridor probabilities set transmission failure frequency is unsupported.
The engineering prior sets frequency; the existing model can redistribute relative
risk under explicit, limited assumptions. This is the chosen conservative prototype,
not an empirically established best predictor.

1. For Darragh, use the shipped complete-feature prediction export at matching UTC
   hours. Otherwise evaluate the checksum-verified saved XGBoost/calibration, using
   its best early-stopping iteration, on the available route features. No network
   request, new labels or retraining occurs.
2. Synthetic gust max/p95 are calculated **before** altitude adjustment; p95 uses
   equal route-sample weights. Previous-three-hour gust excludes the current hour
   and stays missing for the first three hours. Crosswind, rain, temperature and
   pre-window histories are not invented: unknown inputs take XGBoost's missing
   paths. The fallback for other historical storms uses unadjusted UKV route gusts.
   Both differ from complete ERA5 training; metadata reports partial-feature mode,
   missing columns and weather convention. Their calibration is unvalidated.
3. For each line/hour, find other GB lines at raw gust within 10 km/h and engineering hazard
   within a factor of four. Sort by squared scaled gust/log-hazard distance; take
   at most twelve. With fewer than five peers, use no correction. These matching
   widths/support limits are assumptions, not fitted hyperparameters. Only 73 lines
   were in the training region. Geographic transfer to the remaining lines is
   unvalidated and flagged per line, even when their input features are available.
4. The reference is `expit(median(logit(q_peer)))`. It is a leave-one-out statistic
   of current predictions, **not** a learned reference, observed failure frequency
   or new validation result. Matching wind and engineering risk limits obvious
   double counting of gust, length and altitude, but cannot isolate causal effects.
5. Compute the bounded residual multiplier below, then find a common scale by
   bisection over supported lines. Clip each scaled multiplier to [.5,2] and require
   `sum(p_hybrid) = sum(p_engineering)` each hour. Unsupported lines
   keep multiplier one. Beta zero exactly preserves engineering probabilities.

The equality applies to unconditional potential line exposures at that hour,
**before** hardening and excluding no lines for previous outages. It is not equality
of realised weather failures, distinct failed lines, cascades or customer losses:
different failed lines change availability and subsequent recovery. A high raw
corridor score never creates a probability floor. No normalization uses future
outcomes or mitigation results. The multiplier bounds, peer counts, training-domain
flags, expected counts, q/full/reference, applied p and random draws are exported.

The API retains engineering mode for callers omitting `failure_mode`; the UI
explicitly requests hybrid. An explicit uploaded pair selects the separate
unnormalised paired experiment described next. Imported exposure needs engineering
probabilities and raw `ml_weather` features to use the built-in adapter; passing
terrain-adjusted gust as raw ML weather is not silently allowed.

## Optional paired learning

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

Engineering mode uses that line probability. The default built-in hybrid creates
full/reference scores as described above. An optional uploaded paired experiment
uses a fitted full model/reference pair for every branch and matching hour:

```
s = logit(q_full) - logit(q_ref)
m = clip(exp(beta * s), min_multiplier, max_multiplier)
lambda_engineering = -log(1 - p_engineering) / dt
p_hybrid = 1 - exp(-lambda_engineering * m * dt)
```

Numerical logits are clipped at 1e-12. Computation uses stable log/exponential
operations; engineering p=0 stays zero and p=1 stays one. Default beta=.5 and bounds
[.5,2] are **assumptions**. API bounds: beta 0–2, min .05–1, max 1–10. Beta=0
reproduces engineering probability. In the uploaded-pair experiment, equal
full/reference scores also give no correction; the built-in mode additionally
normalizes the supported multipliers to preserve expected frequency. A high ML score does not
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

Every simulated frame reports `node_status`: load-bearing nodes are `supplied`,
`reduced_supply` or `out_of_supply` according to served MW; zero-load nodes are
`energized`/`deenergized` according to generation capacity in their island. This
is a connectivity/capacity proxy, not AC energization or physical substation damage.
The map and status counts use these states, including supply impairments below the
old 0.5 MW display threshold. Ensemble results include ever-out/impaired node counts
and per-node outage probabilities. These count supply states seen at any cascade
round; energy loss still integrates only the settled hourly state. Repairs can
restore supply at subsequent hourly boundaries.

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

The UI runs the built-in hybrid immediately without the first two commands.
The first two training/export commands require the pending feature/label tables;
they cannot reconstruct them from the saved corridor model. Import paired.json,
choose its matching storm and select Experimental hybrid in the UI. Matching branch
order, complete hourly coverage, provenance and fitting cutoff are enforced.
See [HYBRID_RESULTS.md](HYBRID_RESULTS.md) for measured integration results.
