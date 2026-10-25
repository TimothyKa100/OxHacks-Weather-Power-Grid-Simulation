> Current specification: [MODEL_SPEC.md](MODEL_SPEC.md). This document retains earlier model/source details. Raw corridor exports are still display-only; the new explicit paired hybrid mode can affect simulated line failures. No unpublished transmission training results are claimed.

> Ported from the `dataRefininf` branch. This gated feeder pipeline still trains nothing without verified labels. The trained model in this workspace is the separate GB line-corridor model in [GB_LINE_MODEL.md](GB_LINE_MODEL.md).

# Predicting network failures at each timestep

The implemented XGBoost research workflow, actual feature partitions, evidence templates, probability importer and current validation commands are documented in [FAILURE_PIPELINE.md](FAILURE_PIPELINE.md). Training remains gated on verified events and operating/reporting exposure. The broader modelling guidance below also describes later work beyond this implementation.

## Recommended first model

Train an **hourly discrete-time failure-hazard model**. For each operating, observed asset (initially a feeder), predict:

`p(i,t) = P(new unplanned failure in [t,t+1 hour) | operational at t, information available by t)`.

Start with historical-rate and logistic-regression baselines, then gradient-boosted trees (LightGBM or CatBoost). Trees are a sensible first experiment for mixed tabular features and missing values. A temporal graph neural network is a later comparison once historical topology, trustworthy labels and enough independent storms exist; graph complexity cannot recover absent labels. Keep direct physical breakage, protection trips and upstream loss of supply as separate targets. Current incident candidates support at most feeder-level investigations, not confirmed component breakage.

**No model has been trained by this change.** The existing 69,584 candidate incidents have unknown planned status and `training_eligible=false`; the verified label table is empty. Training on them as confirmed failures, or treating all other hours as healthy, would produce misleading scores. The existing IEEE simulation uses synthetic fragility assumptions and is separate from this real-data atlas.

## What exists now

The saved coverage report contains 4,015,413 source asset records, 806,313 topology edges and 2,882,554 other relationships. These are overlapping representations, not unique physical asset counts. The database is about 3.6 GiB. Coverage is Great Britain, not all of the UK: Northern Ireland is missing, some operator inventories are incomplete, and 110,915 records have no representative coordinates. Current source layers are not a historical network reconstruction.

The main sources are operator CIM/LTDS electrical models, NESO ETYS transmission endpoints, OSM mapped equipment/routes, DUKES/REPD generation, SSEN incident candidates, regional ERA5 weather via Open-Meteo, regional NFI woodland exposure, and OS Terrain 50. See `data/grid/README.md`, `data/grid/ATTRIBUTION.md`, `docs/DATA_GUIDE.md` and the source manifests for scope, licences and actual coverage. A national weather request plan exists, but is not downloaded nationwide weather.

## Training rows and labels

Use one row per **asset × UTC hour**, keyed by asset ID, topology version, cutoff time and forecast horizon. Do not manufacture millions of component labels from feeder incidents. At first use operator-confirmed feeder crosswalks; move to component rows only when damage/repair records identify actual components.

A positive row needs a verified new unplanned event in the following interval. Negative rows require affirmative reporting coverage, an in-service asset and known operational exposure for the full interval. Censor unknown coverage, planned maintenance, commissioning/retirement, ongoing failure and uncertain restoration. Retain event IDs and storm IDs for deduplication and grouped splits. Keep targets outside feature tables. Distinguish an onset from a later report timestamp. Resolve source timezone and DST rather than silently shifting ambiguous incidents.

For every observation record both **valid time** (when it describes the world) and **available time** (when the prediction system could have known it). An as-of join must require available time ≤ prediction cutoff. For weather forecasts retain issue time, valid time, lead time and model version. A forecast for the coming hour is usable if issued before the cutoff; a retrospective weather value for that hour is not. ERA5 supports retrospective risk studies, not a claimed operational forecast backtest.

## Features, relationships and gaps

| Feature family | Candidate predictors with units | Available evidence / missing inputs |
|---|---|---|
| Asset characteristics | Detailed subtype, overhead/underground, route length km, voltage kV, material, age, condition, maintenance age | Type, selected lengths/voltages and source properties exist. Age, materials and condition are incomplete; preserve nulls and missing indicators. |
| Electrical | R/X Ω, B S, rated MVA, generation capacity MW, transformer winding/tap data, seasonal limits with original units | Published fields exist for parts of the grid. Capacity is not actual loading; A and MVA are not interchangeable. Unknown per-unit bases must remain unconverted. |
| Weather | Route maximum gust km/h; wind direction relative to route; precipitation mm/h; air temperature °C; lagged 6/24/72 h maxima, totals, threshold counts and excess exposure | Regional hourly weather and lagged features exist. Nationwide sampling, archived forecasts and fine-scale validation are incomplete. |
| Landscape | Terrain mean/min/max m, slope, woodland-buffer fraction, coast exposure, flood depth relative to asset floor | GB terrain exists. Woodland is regional, static 2024 exposure. Flood depth, floor level, tree height/health and conductor clearance are missing. |
| Operating state | Load/rating ratio, energised state, known outages, time since verified repair, switching configuration | Historical telemetry, switching and protection settings are largely missing. Do not use future event outcomes. |
| Graph context | Verified degree, alternate supply paths, upstream/downstream roles, known neighbor outages at cutoff | Source-specific topology exists. Membership/support/candidate links are separate. OSM topology is mapped, not operator-certified. Historical state and identity crosswalks remain incomplete. |
| Provenance/quality | Source, spatial resolution, weather coverage, distance from samples, parameter availability, topology version | Retain these for audits and uncertainty; test whether source identifiers cause spurious shortcuts. |

Useful interactions to test include **gust × woodland exposure**, **rain accumulation × soil saturation × tree exposure**, **wind direction × conductor orientation**, **temperature × loading/rating**, and **flood depth × equipment floor clearance**. These are physical hypotheses, not fitted causal effects or established damage thresholds. Missing partner inputs make an interaction unavailable. Current 80/100/120 km/h gust cutoffs are exploratory features, not universal breakage limits.

Do not put incident cause, damaged equipment codes, duration, repair completion, affected customers or post-event load into pre-event predictors. Historical aggregates must exclude the current/future event. A current terrain or woodland snapshot used in older years needs an explicit historical-proxy flag. Fit imputation, encodings, scaling and feature selection only on the training period.

## Training and evaluation protocol

1. Resolve labels, availability intervals and historical asset identity for one operator region. Audit a sample with the source owner before expanding.
2. Build versioned hourly feature/label partitions, with UTC cutoffs and a reproducible as-of join. Validate negative exposure and missingness by region and year.
3. Divide chronologically into training, validation, calibration and untouched test periods; keep whole storms together and include quiet periods. Purge boundaries by at least the largest lookback (currently 72 h) plus forecast horizon. Separately hold out geographic areas/feeders to measure transfer. Random row splits leak storm and asset context. Plain TimeSeriesSplit alone does not implement storm grouping or a spatial holdout.
4. Compare historical rates, wind-only logistic regression, then boosted trees. Tune on validation only. Use native missing handling or train-only imputation. If downsampling healthy hours, retain inclusion probabilities and use weights to recover the true prevalence; do not calibrate on a balanced artificial test set.
5. Calibrate on a separate representative period, choosing a method supported by the number of observed events. Evaluate reliability diagrams, Brier score/log loss, average precision, recall at a fixed inspection budget, false alerts per 1,000 asset-hours and storm-level failure counts. Accuracy alone is unhelpful for rare failures. Bootstrap by storm, not independent asset-hours, for uncertainty.
6. Compare calibration and recall across asset types, areas, storm severity and previously failed versus never-failed assets. Freeze a model version and feature contract before test evaluation. Monitor subsequent drift.

Calibration matters because the output drives simulation. LightGBM explicitly warns that class weighting can worsen individual probability estimates: [LightGBM parameters](https://lightgbm.readthedocs.io/en/latest/Parameters.html). Reliability curves complement Brier/log loss, which also measure discrimination: [scikit-learn calibration](https://scikit-learn.org/stable/modules/calibration.html). Chronological validation avoids training on future observations: [TimeSeriesSplit reference](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).

## Using probabilities in the simulator

At each hour, obtain probabilities only for operating assets, sample new direct failures, update connectivity, solve feasible electrical flows, apply separately validated protection/overload rules, and update repair states. A disconnected asset is not necessarily broken. Recompute operating features before the next hour. Track the cause of each state change.

Do not declare every asset with probability above 50% broken; rare failures often have much smaller individual probabilities. Run many trajectories. Independent Bernoulli draws are only a baseline: storms create shared failure dependence. Introduce a storm-level random effect or spatially correlated residual model, and validate joint failure counts. A graph risk model should not silently count the same propagated outage again as direct physical damage.

For a constant hazard rate, rescale probability from horizon h to delta by `1 - (1-p_h) ** (delta/h)`; this assumption is not valid when weather or operating state changes within the interval. Accumulate multi-hour risk through survival probabilities with the relevant changing conditional states.

## Storage recommendation

**Yes, keep the database—but not all training time series in one huge relational table.** The existing SQLite + RTree database is appropriate for local asset inspection and topology traversal and is reused by the atlas. Keep raw source snapshots immutable and preserve stable source IDs, licence, retrieval date, checksums and units.

Use SQLite now for assets, versioned relationships, event/availability metadata, model registry and prediction-run manifests. Move to PostgreSQL/PostGIS when concurrent writers, multi-user services or managed spatial operations justify it. A graph database is unnecessary for the current workload; indexed edge tables already support adjacency queries.

Store weather, asset-hour features, labels and batch predictions as **partitioned Parquet**, e.g. dataset/version/operator/year/month, and query with DuckDB or an offline training reader. Do not create a tiny file for every asset/hour. Store meteorology once per native grid cell and time; use a route-to-grid crosswalk rather than duplicating full weather for every asset. Keep topology versions separate from time-varying operating state.

At 4,015,413 records × 8,760 hours, a naive expansion would exceed **35 billion rows/year**, before correcting duplicates or adding features. Start with a verified regional feeder cohort and exposure intervals; materialize only the experiments needed. Preserve full representative evaluation exposure even if training negatives are sampled.

Suggested table contracts:

- `asset_version(asset_id, source, subtype, valid_from, valid_to, geometry, electrical_fields, provenance)`
- `relation_version(from_id, to_id, relation_type, evidence, topology_version, valid_from, valid_to)`
- `event(event_id, asset_id, onset_utc, reported_utc, restored_utc, planned_status, physical_damage_status, verification_status)`
- `availability(asset_id, start_utc, end_utc, in_service, reporting_complete, state_quality)`
- `weather(cell_id, valid_time, available_time, issue_time, variables, units, source_version)`
- `feature_partition(asset_id, cutoff_utc, horizon_hours, topology_version, feature_version, predictors...)`
- `label_partition(asset_id, cutoff_utc, target, at_risk, censor_reason, event_id)`
- `prediction(asset_id, cutoff_utc, horizon_hours, probability, model_version, data_quality)`

None of these proposed training tables implies that missing source evidence has been collected. Priorities are verified event status/onset/restoration, reporting intervals and historical identity; then weather coverage, operating state and component-level condition data.
