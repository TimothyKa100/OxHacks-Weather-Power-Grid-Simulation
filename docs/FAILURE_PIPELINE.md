> Ported from the `dataRefininf` branch. This gated feeder pipeline still trains nothing without verified labels. The trained model in this workspace is the separate GB line-corridor model in [GB_LINE_MODEL.md](GB_LINE_MODEL.md).

The implemented workflow is an hourly SSEN feeder research pipeline. It assembles route weather, asset, woodland and terrain features without turning unverified incidents into failure labels. The separate IEEE simulator accepts explicitly identified branch probabilities. A feeder prediction cannot be imported as a physical GB line failure.

**Outputs and current status**

`data/failure_model/v1/` is the SEPD dataset. `data/failure_model/v1_shepd/` is the SHEPD expansion. Both use `ssen-hourly-v1` and end-exclusive UTC intervals. The build covers 1 April 2015 through 18 October 2026. Each region has its own source checksums, monthly partitions and evidence gate.

| Output | Contract |
|---|---|
| `assets.parquet` | One row per candidate GIS-matched feeder; source IDs, route length, voltage, woodland, integrated terrain and snapshot dates |
| `weather_audit.parquet` | Every file's exact time range, gaps/duplicates, variable coverage/ranges, constant-zero soil flag and checksums |
| `roster_weather_crosswalk.parquet` | Refreshed coverage for the full roster, preserving previous availability flags |
| `route_weather_pieces.parquet` | Consecutive route intervals, true geographic bearings, source segment IDs and length weights |
| `route_weather_links.parquet` | Feeder/request-cell route fractions and bearing sets |
| `weather_requests_pending.parquet` | Actual missing request cells; absence is not zero weather |
| `incident_associations.parquet` | Original candidate evidence associated with cohort feeders; reported time is not certified onset |
| `incident_weather_research.parquet` | Descriptive join to features at provisional reported hours; explicitly ineligible for training |
| `features/YYYY-MM.parquet` | One feeder × UTC cutoff hour; predictors only; one-hour horizon |
| `labels/YYYY-MM.parquet` | Separate keys, nullable target, verified exposure/label flags and censor reason |
| `storm_windows.parquet` | Exploratory weather clusters and quiet-period grouping; not an official storm catalogue |
| `evidence/verified_events.parquet` | Initially empty typed contract for genuinely verified source events |
| `evidence/availability.parquet` | Initially empty typed contract for verified coverage and state history |
| `evidence/source_review_queue.parquet` | Original incident identifiers and required evidence for operator/source review, including voltage consistency flags |
| `inventory_report.json`, `feature_manifest.json`, `incident_report.json`, `label_report.json` | Counts, provenance, run configuration and explicit readiness |
| `validation_report.json`, `training_readiness.json` | Integrity/recomputation checks and reasons model fitting is unavailable |

The source incident export has no planned flag, onset confirmation, verified restoration or complete historical reporting/state intervals. Neither cause wording nor average customer interruption duration fills those gaps. Current network/2024 woodland snapshots are explicitly historical proxies. No empirical failure model is fitted while the evidence gate is closed.

The completed run and remaining data dependencies are recorded in [FAILURE_PIPELINE_RESULTS.md](FAILURE_PIPELINE_RESULTS.md). Regenerate that handoff after both regional runs with `.venv/bin/python scripts/summarize_failure_dataset.py`.

**Reproduce the data work**

Install into the project virtual environment:

```bash
.venv/bin/python -m pip install -r requirements-model.txt
```

Run from the repository root. The `--output` option goes before the subcommand. Feature generation resumes completed months only when all input/configuration fingerprints agree; `--rebuild` replaces only the generated feature and label partitions in that output directory.

```bash
.venv/bin/python -m failure_model inventory --operator SEPD
.venv/bin/python -m failure_model incidents
.venv/bin/python -m failure_model features
.venv/bin/python -m failure_model storms
.venv/bin/python -m failure_model link-incidents
.venv/bin/python -m failure_model labels
.venv/bin/python -m failure_model validate
.venv/bin/python -m failure_model readiness
```

For the northern expansion:

```bash
.venv/bin/python -m failure_model --output data/failure_model/v1_shepd inventory --operator SHEPD
.venv/bin/python -m failure_model --output data/failure_model/v1_shepd fetch-weather --include-roster
.venv/bin/python -m failure_model --output data/failure_model/v1_shepd inventory --operator SHEPD
.venv/bin/python -m failure_model --output data/failure_model/v1_shepd incidents
.venv/bin/python -m failure_model --output data/failure_model/v1_shepd features
.venv/bin/python -m failure_model --output data/failure_model/v1_shepd storms
.venv/bin/python -m failure_model --output data/failure_model/v1_shepd link-incidents
.venv/bin/python -m failure_model --output data/failure_model/v1_shepd labels
.venv/bin/python -m failure_model --output data/failure_model/v1_shepd validate
```

`fetch-weather` downloads only missing public request cells. It preserves source parameters, units, returned coordinates and raw hashes, stops on rate limiting, and records failed requests. Inventory must be refreshed after downloads. It does not obtain restricted outage or operational records. Run `.venv/bin/python scripts/refresh_weather_coverage.py` to refresh the original delivery report, roster crosswalk, example snapshot and existing SQLite feeder-weather links in one transaction per output. The graph's other source records and electrical topology are not rebuilt.

For a small separate experiment:

```bash
.venv/bin/python -m failure_model --output /tmp/ssen-pilot inventory --operator SEPD
.venv/bin/python -m failure_model --output /tmp/ssen-pilot incidents
.venv/bin/python -m failure_model --output /tmp/ssen-pilot features --start 2021-11-20T00:00:00Z --end 2021-12-05T00:00:00Z
```

**Spatial and temporal feature definitions**

The prediction unit is a feeder with a candidate one-to-one GIS circuit association. Published supply relationships do not establish switch connectivity. Electrical-model R/X, ratings, loading and source-scoped OSM representations are not guessed into that association.

Consecutive elevation-profile route points are converted to geographic coordinates. Each interval's midpoint selects a 0.5-degree request cell. Interval contributions are reconciled to the original BNG segment length, avoiding the small length differences caused by independently rounded geographic coordinates. Bearings are geographic, reduced modulo 180 degrees because the crosswind formula uses an absolute sine. Terrain means integrate height along distance; incomplete terrain profiles yield null whole-route summaries.

Weather is stored once per request point. Route gust maximum and length-weighted 95th percentile describe the spatial sample distribution, not measured peak gusts at every conductor. Crosswind is the maximum of `gust × abs(sin(direction − bearing))` over route interval bearings. Integer-degree wind direction allows an exact precomputed directional envelope. The 10 m gust convention is raw weather-grid gust: the simulator's separate engineering terrain multiplier is not applied.

At cutoff `t`, instantaneous gust/wind/temperature/soil predictors use samples strictly before `t`. Precipitation timestamped `t` is the total for `[t−1h,t)`; 24/72-hour precipitation sums include that completed interval. This deliberately fixes the one-hour offset that would arise from applying the same shift to every weather variable. Source: [Open-Meteo historical variable definitions](https://open-meteo.com/en/docs/historical-weather-api#hourly-parameter-definition).

Rolling features require complete windows. Missing data and the initial 72-hour warmup remain null. Constant-zero soil series are marked unusable, without replacing legitimate zero rainfall. Mixed soil-zero series remain source observations requiring further quality investigation. Snow depth is excluded because it is wholly absent. The full ordered feature list is `failure_model.common.FEATURES`; quality and event outcome fields are not predictors.

The 49 predictors include the complete 6/24/72-hour count set for gusts at 80/100/120 km/h, route-mean temperature at or above 30°C or at or below −5°C, and route-mean completed-hour precipitation at or above 10 mm. Their 24-hour excess-exposure sums preserve the original units. These cutoffs are exploratory features, not calibrated equipment damage limits. Temperature and precipitation threshold features use route length-weighted means; gust thresholds use the spatial route maximum.

ERA5 is retrospective reanalysis. Its timestamps describe meteorological validity, not information available to a live forecaster. No operational issue time is fabricated. A future live adapter needs archived forecast issue/valid/lead times, matched training features and separate forecast validation.

**Evidence required to open training**

Populate the typed Parquet templates under `evidence/` only with operator/source-verified evidence. Their exact fields and allowed values are in `evidence/contract.json`.

- Events require unique event ID, original source event key when applicable, verified feeder identity, onset, planned classification, cause class and a real evidence reference. Unknown restoration stays null. Include planned and non-weather interruptions so their downtime is censored.
- Availability requires nonoverlapping intervals, in-service status, complete reporting and state history, confirmed initial operating state, verified historical identity and evidence reference. A current roster cannot supply these assertions.
- Resolve all candidate incidents falling within a claimed complete interval. Unresolved timestamps or incidents censor that interval instead of producing healthy labels.
- `any_unplanned` models interruption risk conditional on weather. `--target wind_weather` additionally requires verified weather cause. Direct breakage, protection trips and upstream loss remain distinct concepts; no component labels are manufactured.

An onset at 13:24Z labels `[13:00Z,14:00Z)`. Hours beginning during downtime are censored until verified restoration. Planned/competing interruptions are excluded. Unknown restoration censors subsequent exposure. Separate evidence of operating state can be supplied in later intervals; the current implementation remains conservative about unresolved prior restoration.

```bash
.venv/bin/python -m failure_model labels --target any_unplanned
.venv/bin/python -m failure_model readiness
.venv/bin/python -m failure_model train --model-dir data/failure_model/models/ssen-v1
```

The last command exits with code 2 and an explicit explanation when verified positives/negative exposure are absent. It does not create a model artifact. Training also requires at least ten positive and ten negative examples in each fitting/tuning/calibration/test partition; this is a basic software guard, not a statistical sufficiency claim.

**Training, calibration and evaluation**

The fixed chronological periods are training through 2021, tuning in 2022, calibration in 2023, untouched temporal testing in 2024–2025, and monitoring in 2026. Rows within 73 hours of boundaries are purged on both sides. Entire weather clusters touching a boundary are purged. A deterministic 20% primary-area hash holdout is excluded from all fitting/tuning/calibration and evaluated separately during the test period. Quiet periods remain represented.

Training keeps all positives and samples 2% of training negatives by default, retaining inverse inclusion weights. Tuning/calibration/test exposure stays complete. Features are not expanded into circuit-component labels. The baseline comparisons are the weighted historical rate and a wind/length/voltage logistic model with training-only imputation/scaling. Four shallow, regularized XGBoost candidates use `binary:logistic`, histogram trees, early stopping and tuning log loss. Probability output is not thresholded. [XGBoost objective reference](https://xgboost.readthedocs.io/en/stable/parameter.html#learning-task-parameters)

Sigmoid calibration is fitted to model margins on the separate representative calibration period. No default cross-validation mixes storms or years. [Calibration reference](https://scikit-learn.org/stable/modules/calibration.html)

Frozen model directories contain the XGBoost model, calibrator JSON, checksums, ordered feature contract, fitting cutoffs and held-out predictions. Evaluation includes Brier/log loss, average precision, reliability bins, false alerts per 1,000 feeder-hours, recall at ten inspections/hour, storm totals and storm/quiet-group bootstrap uncertainty. It separates temporal/geographic transfer and previously failed/never-failed training assets. These uncertainty intervals do not establish independence of line failures.

```bash
.venv/bin/python scripts/evaluate_predictions.py data/failure_model/models/ssen-v1/held_out_predictions.parquet --output /tmp/ssen-evaluation.json
.venv/bin/python -m failure_model predict --model-dir data/failure_model/models/ssen-v1 --features data/failure_model/v1/features/2024-12.parquet --issued-at 2026-10-24T12:00:00Z --destination /tmp/feeder-predictions.parquet
```

Prediction refuses an incompatible feature contract, changed model/calibration checksum, missing route gusts, or cutoffs before the calibration data cutoff. Outputs are explicitly feeder-level historical replay. A forecast or a physical-line prediction needs a different validated contract.

**Simulator integration**

See `TEAM_INTEGRATION.md` for the implemented JSON contract. The existing synthetic IEEE mode retains its original behavior. Imported hourly branch probabilities replace the fragility calculation; they are not multiplied by exposure, pylon count or fragility again. Imported reinforcement uses the explicit assumed hazard reduction `1 − (1 − p)^0.25`; its causal effectiveness is not learned from these incidents. The synthetic legacy mode retains its documented probability multiplier.

The electrical simulator still has no repairs, real GB operating point or verified feeder-to-branch crosswalk. Imported scenarios use shared uniform draws between policies and independent Bernoulli draws conditional on supplied hourly probabilities. Residual storm dependence remains future work. Whole-event weather-or-cascade frequencies are distinct from hourly direct-failure inputs.

**Validation**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m failure_model validate
```

Tests exercise future-data perturbations, completed-hour precipitation, missing-window handling, weighted quantiles, crosswind geometry, vectorized/scalar agreement, planned/competing events, unknown restoration, unavailable reporting exposure, chronological storm purging, training refusal, and simulator import/reinforcement behavior. A small explicitly synthetic fixture checks XGBoost/calibration/artifact production in pytest's temporary directory; it is not an empirical trained model or a deployment artifact. Dataset validation additionally checks every monthly cohort/key partition and independently recomputes selected asset-months from source weather.
