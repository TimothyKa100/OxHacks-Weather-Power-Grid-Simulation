> Current specification: [MODEL_SPEC.md](MODEL_SPEC.md). This document retains earlier model/source details. Raw corridor exports are still display-only; the new explicit paired hybrid mode can affect simulated line failures. No unpublished transmission training results are claimed.

# GB line-corridor outage-risk model (XGBoost)

This model feeds hourly probabilities for every GB map branch into the existing
simulator through the weather/ML importer. The simulator still does the failure draws,
DC redistribution, overload trips, repairs and mitigation comparison.

**What the probability means.** For branch *i* and hour *t*:

> P(at least one new weather-caused overhead-line fault is reported by SSEN Distribution
> with a candidate location inside branch *i*'s 5 km corridor during [t, t+1h))

This is an **area-level outage-risk proxy** for the ground each transmission line crosses.
It is not a calibrated transmission-line failure probability: none of the outage sources
identifies a failed transmission asset (see *Why an area-level target* below).

## Why an area-level target

The refined outage register (`data/grid/normalized/all_outage_reports.parquet`, 197,955
reports) was checked for anything that links a report to one of the 367 GB branches:

- `asset_failure_verified` and `training_eligible` are 0 for every row; `failed_asset_id` is empty.
- Only SSEN's NaFIRS export (SEPD/SHEPD, 69,237 reports, April 2015–October 2026) has a cause,
  equipment class and voltage. Its locations are candidate feeder/circuit representative
  points, not fault positions.
- Weather-caused faults on 132 kV overhead lines number **24 in 11.5 years**, all located at a
  circuit's representative point. That is too few, and too loosely located, to label GB-line
  failures (a 75/25 split would leave about 6 test events).
- NGED, UKPN, NPG, ENWL and SPEN rows come from scraped live feeds from 2021 onward, without
  a weather-cause field and with collection gaps. A missing report there does not establish a
  negative. SPEN's historical 132 kV/EHV fault datasets exist only as metadata (anonymous export
  returned `[]`).

So, as the brief recommends, the model predicts area-level outage risk, keyed to each
simulator branch so it plugs into the importer.

## Training rows

One row per **simulator branch × UTC hour** (`asset_id` = `L001`..., the
`/api/network?name=gb` IDs), built by `python -m failure_model gb-dataset`:

| Column | Meaning |
|---|---|
| `asset_id`, `timestamp_utc` | Branch and interval start `t`; the interval is [t, t+1h) |
| `storm_id` | Severe-wind window (`wind-YYYYMMDD`) or `quiet-YYYY-MM` |
| features | Listed below |
| `failed_next_hour` | 1 if a new deduplicated incident's onset hour is `t` |

- **Training domain:** branches whose 5 km corridor is at least 90% inside SSEN primary
  supply areas (73 of 367: 46 × 132 kV, 15 × 275 kV, 12 × 400 kV). Only there does the
  continuous NaFIRS register make an hour without a report a negative. Monthly report counts
  have no gaps (minimum 28 per licence area per month).
- **Positives:** SEPD/SHEPD reports with `equipment = Overhead Lines` and a weather cause
  (wind and gale, windborne material, falling live/dead trees, lightning, ice,
  snow/sleet/blizzard, rain, freezing fog/frost, flooding, solar heat), excluding reports
  flagged `possible_operational_or_planned`. Timestamps are the register's provisional
  Europe/London→UTC conversion.
- **Deduplication:** reports for the same feeder reference within 6 h are one incident, and
  the first report is taken as the onset. 17,881 reports become 15,711 incidents. An incident
  inside overlapping corridors is a positive for each of them (area target).
- **Availability:** no transmission availability or outage records exist, so no hours can be
  excluded as "already unavailable". For an area target, later incidents in the same corridor
  are new events.
- **Period:** 2015-04-02 to 2026-10-18 (first day reserved for 24 h history). That gives
  7,388,184 rows and 6,985 positives, a base rate of 0.095% per line-hour.

## Features and conventions

| Feature | Definition |
|---|---|
| `gust_max_kmh` | Maximum ERA5 gust over the route's weather cells for the interval |
| `gust_p95_kmh` | Length-weighted 95th percentile of the route's cell gusts |
| `gust_prev_3h_max_kmh` | Route maximum gust over the three hours before `t` |
| `crosswind_max_kmh` | max over ~2 km route segments of gust × \|sin(wind direction − segment bearing)\| |
| `rain_1h_mm` | Length-weighted route precipitation during the interval |
| `rain_prev_24h_mm` | Length-weighted route precipitation over the 24 h before `t` |
| `temperature_c` | Length-weighted route 2 m temperature at the end of the interval |
| `length_km`, `ele_mean_m`, `ele_max_m`, `kv`, `circuits` | From `data/gb_network.json` |
| `vegetation_fraction` (ablation) | Share of route within 30 m of NFI 2024 woodland |
| `pylons`, `span_m`, `tower_height_m` (ablation) | Estimated from length and voltage class |

- **Gust convention:** raw ERA5 10 m gust (maximum over the preceding hour), from the Open-Meteo
  archive API. There is **no terrain adjustment**, unlike the engineering model's altitude
  factor. The same request is used for training and inference.
- **Time alignment:** ERA5 gust and precipitation stamped *s* cover (s−1h, s], so the interval
  [t, t+1h) uses stamp t+1. Wind direction and temperature are the instantaneous values at t+1.
  Histories end at t. This replays reanalysis as a perfect hourly forecast; operational use
  needs a forecast for each hour, issued before it.
- **Route sampling:** each route is split into ~2 km pieces; each piece uses the 0.5° lattice
  cell nearest its midpoint (the archive's lattice). Bearings are geodesic, modulo 180°.
- **Missing values stay missing.** Age, maintenance, conductor type and condition have no
  genuine records and are not invented. Woodland was downloaded only around the two SSEN
  regions, so `vegetation_fraction` is missing for 143 of 367 branches.
- **Wind features are constrained to be monotone increasing.** Holding everything else fixed,
  stronger wind cannot lower risk. This is a physical prior.

## Weather source audit

`scripts/audit_weather_sources.py` compares sources with independent NOAA ISD station gusts
(nine stations in southern Scotland, northern England and NI; quality codes 0/1/4/5), each
taken as the maximum of the hour ending at the stamp.

| Storm (window) | Source | Stations | Mean bias | MAE | Correlation | Peak ratio |
|---|---|---:|---:|---:|---:|---:|
| Éowyn (23–25 Jan 2025) | ERA5, 0.5° lattice cell (model input) | 7 | +3.8 km/h | 8.9 | 0.88 | 0.98 |
| | ERA5 at the station point | 7 | +5.0 | 9.5 | 0.88 | 0.99 |
| | Met Office UKV 2 km (workspace storm grid) | 5* | +3.8 | 8.4 | 0.92 | 1.05 |
| Arwen (26–27 Nov 2021) | ERA5, 0.5° lattice cell | 7 | +12.2 | 14.0 | 0.60 | 1.10 |
| | ERA5 at the station point | 7 | +13.3 | 14.8 | 0.65 | 1.16 |

\*UKV storm grids do not cover Northern Ireland. Shap and Eskdalemuir reported no gusts in
these windows. Per-station values are in `data/failure_model/weather_audit.json`.

**Decision:** ERA5 via Open-Meteo, fetched on demand. It is the only source with continuous
2015–2026 history and the wind direction, precipitation and temperature the features need, and it
reaches any GB location. The archive's 87 full-period cells cover the training domain. Other
GB cells (83 more of the 136 that GB routes touch) are fetched for each replay window by
`failure_model/gb_weather.py`, using the archive's exact request. UKV is slightly sharper during
storms but exists only for the eight built storm windows (2024–2026) and has gusts only. The
stations are point observations for two years, so they are kept for validation.

## Split, tuning and calibration

Chronological by hour, with no shuffling:

| Part | Share of hours | Period | Use |
|---|---|---|---|
| fit | 0–60% | 2015-04-02 → 2022-03-05 (4.43M rows, 3,996 events) | Tree fitting |
| tune | 60–67.5% | 2022-03-07 → 2023-01-15 (0.55M, 414) | Early stopping, hyperparameters, feature-set choice |
| calibrate | 67.5–75% | 2023-01-17 → 2023-11-27 (0.55M, 563) | Platt scaling on the model margin |
| **test** | **75–100%** | 2023-11-29 → 2026-10-17 (1.85M, 2,008) | Untouched evaluation |

The first 75% of hours is used for development and the final 25% is the test. A boundary
falling inside a severe-wind window moves to that window's start, so whole storms stay on one
side. Rows within 25 h of each boundary (24 h lookback + 1 h horizon) are purged. Training
uses every row at the real failure frequency: no oversampling and no `scale_pos_weight`. The
output is `predict_proba` after calibration, and it is never thresholded.

## Results on the untouched test period

Model `gb-corridor-xgb-v1`: depth 5, `min_child_weight` 50, 315 trees (early stopping on
tune). Training cutoff `2023-11-28T18:00Z`. Artifacts, including the manifest, evaluation and
reliability plot, are in `data/failure_model/models/gb-corridor-xgb-v1/`.

| Test period (1,845,221 line-hours, 2,008 events) | Log loss | Brier | Average precision | Expected events |
|---|---:|---:|---:|---:|
| **Calibrated XGBoost** | **0.00650** | **0.001041** | **0.076** | **1,892** |
| Uncalibrated XGBoost | 0.00652 | 0.001042 | 0.076 | 1,587 |
| Wind-only logistic (gust, 3 h gust, length, kV) | 0.00702 | 0.001067 | 0.043 | 1,577 |
| Constant training-period rate | 0.00853 | 0.001087 | 0.001 | 1,664 |

- Brier skill over the training rate is +4.2% (storm/quiet-group bootstrap 95% Brier interval
  0.00075–0.00144). Log loss is 24% lower, and average precision is 70× the event rate.
- `scripts/evaluate_predictions.py` on the 21 held-out storm windows (98,915 line-hours,
  950 events) gives Brier 0.00888, log loss 0.043 and Brier skill +7.3%.
- **Reliability** is close to the diagonal from 10⁻⁴ to 10⁻² per hour (for example, 0.00056
  predicted against 0.00055 observed). Above 1% the model is too low: 0.016→0.020,
  0.051→0.072, 0.159→0.226.
- **Storm hours are under-predicted:** 545 expected against 950 observed. Quiet hours are
  over-predicted: 1,347 against 1,058. The largest misses are leaf-on or exceptional storms:
  Amy (Oct 2025) 175 against 57, Floris (Aug 2025) 147 against 35, and Éowyn (Jan 2025) 111
  against 45. The pattern was **already visible in development data**: in the calibration
  period, storm hours ran 271 observed against 122 expected, and in the fit period leaf-on storm
  hours ran 322 against 219. A single Platt scaling cannot correct a storm/quiet shift, and the
  spec feature list has no season. A seasonal feature (month or leaf-on), chosen on tune data, is
  the clear next version.
- **By voltage:** 132 kV corridors 887 observed against 703 expected; 275 kV 500 against 490;
  400 kV 621 against 699.
- **Feature importance (gain):** crosswind, route p95 gust, route maximum gust, 3 h gust history,
  kV, length. Rain and temperature contribute little.
- **Ablations** (same hyperparameters, chosen on tune log loss):
  - `+vegetation_fraction`: tune log loss 0.005011 against 0.005029, a 0.35% gain. It is not
    deployable because woodland is missing for 143 of 367 branches.
  - `+pylons/span_m/tower_height_m`: tune 0.005033, no gain. These are deterministic in
    length and voltage.

  The deployed model therefore uses the spec features without vegetation.

## On the map: the ML power-cut forecast and corridor-risk layers

Every storm-plan run includes `power_cut_forecast` (`backend/forecast.py`): the v2 probability per
overhead route and hour on the run's own network, and, for historical storms, the comparison with
reported cuts. The map picks **ML power-cut forecast (this run)** after a run. The halos follow the
playback hour during the storm and show the storm total afterwards. Hovering a line shows its
storm chance, plus its expected and reported cut-hours inside the feed's coverage. The panel
charts forecast and reported corridor-hours per hour on the same scale.

Archived exports are also shown as their own layer, **not** fed into the line-failure simulation. Choose a
layer under **Corridor outage risk** below the map. Exports in `data/failure_model/exports/`
are listed via `GET /api/corridor-risk`; a gb-export file loaded with the weather/ML importer
also becomes this layer. Purple halos under each route show risk on a log scale from 0.01% to 30%.
When the replayed storm's hours overlap the layer (for example, Darragh), the halos follow the
playback hour. Otherwise they show storm-total risk, 1 − ∏(1 − p). The engineering line model,
failures, cascades and real outage dots are unchanged. The simulator refuses these files as
weather (`prediction_unit: line_corridor_area_risk`), so they cannot become line-failure
probabilities by accident.

## Exporting a layer

```bash
.venv/bin/python -m failure_model gb-export --start 2024-12-06T12:00Z --hours 60 \
    --api http://127.0.0.1:5001 --destination data/failure_model/exports/darragh-2024.json
```

This produces a file in the importer's hourly format: `source`, `branch_ids` in `/api/network?name=gb` order,
`prediction` (`model_version`, `training_cutoff`, `issued_at`), and hourly `timestamp`,
per-branch `gust_kmh` (route maximum ERA5 gust), per-branch `rain_mm_h` and
`failure_probability`. It is validated with `backend.weather.validate_weather` before writing.
Placing it in `data/failure_model/exports/` makes it available as a map layer. The comparison
below fed it to `POST /api/storm-plan` as line-failure probability (before that route was
closed) to show why it must stay a separate layer.

### Storm Darragh replay (6 Dec 2024 12:00Z, 60 h, inside the test period)

> These numbers were measured on the earlier 367-branch network and engine. The `cascade-realism` network has 673 branches (see CASCADE_MODEL.md). The export was regenerated for it, with zero corridor risk on cables, transformers and links. Corridor risk is shown only as a map layer.

`data/failure_model/exports/darragh-2024_gb-corridor-xgb-v1.json` was exported with branch
order read from the running app. ERA5 for all 136 route cells was fetched on demand; the 53
cells also in the archive matched it exactly. The profile was accepted by `POST /api/storm-plan`
(seed 26, 32 samples, budget 5, crews 6) and compared with the engineering model on the same
storm:

| Darragh, held-out simulation | Engineering route/pylon model | Corridor-risk XGBoost as line probability |
|---|---:|---:|
| Mean lines failed | 0 | 165 of 367 |
| Mean unserved energy | 0 MWh | 432,632 MWh |
| Recovered within 60 h | 100% | 0% |

The model gives 138 corridors a storm-total probability above 0.5 of at least one distribution
fault. That is plausible for distribution faults; Darragh caused thousands of NGED reports. On
the 294 lines outside the training domain, the corridor ranking separates corridors with and
without NGED reports during Darragh at **AUC 0.75**, so the spatial signal transfers. But
transmission lines did not fail at this rate. **Feeding area risk in as line-failure probability
grossly overstates transmission damage**, as the brief warned. Show it alongside the engineering
line model, or map it to line failures with a separately justified, labelled conversion, before
using it for decisions.

## Model v2 (`gb-corridor-xgb-v2`): fine-tuned for the hybrid

`failure_model/gb_v2.py` (`python -m failure_model gb-train-v2`) retrains on the same rows,
target, chronological split and 25 h purges as v1. Its job is to feed the bounded hybrid
(`backend/hybrid.py`), which turns it into the line-failure probability the cascade engine draws.

**Asset identity.** The rows were built on the 367-branch network at commit `0bb65f8`; the
current network has 673 branches and reuses `L` IDs for different routes (only 27 of the 73
training IDs still lie on a training route). `training_routes.json` stores the training routes
as lon/lat geometry. At inference an overhead branch is in the training domain when at least
80% of its route points lie within 2 km of a training route of the same voltage
(`backend/ml_risk.training_domain`): 84 of 436 overhead routes on the current GB network.
The ML signal applies only to overhead routes; cables, transformers and inferred links keep
their engineering hazard.

**Features.** The comparison added month sine/cosine, the previous-6 h maximum route gust
and the previous-6 h accumulated gust excess above 80 km/h. Previous-24 h rain is kept. No
age, condition or vegetation data were guessed.

**Small comparison** (learning rate 0.05, `reg_lambda` 5, up to 800 trees, early stopping 30,
no class balancing; tuning period 2022-03-06 to 2023-01-16, 414 events):

| Features | Depth | Min child weight | Trees | Tune log loss | Top-10 recall | Storm expected / observed | Quiet expected / observed |
|---|---|---|---|---|---|---|---|
| v1 | 3 | 20 | 529 | 0.005032 | 0.536 | 14.8 / 24 | 348 / 390 |
| v1 | 3 | 50 | 503 | 0.005036 | 0.527 | 14.9 / 24 | 353 / 390 |
| **v1** | **5** | **20** | **255** | **0.005026** | 0.539 | 14.8 / 24 | 349 / 390 |
| v1 | 5 | 50 | 316 | 0.005029 | 0.517 | 14.7 / 24 | 351 / 390 |
| expanded | 3 | 20 | 344 | 0.005100 | 0.553 | 14.6 / 24 | 338 / 390 |
| expanded | 3 | 50 | 388 | 0.005095 | 0.563 | 15.7 / 24 | 341 / 390 |
| expanded | 5 | 20 | 131 | 0.005101 | 0.548 | 15.0 / 24 | 341 / 390 |
| expanded | 5 | 50 | 193 | 0.005105 | 0.556 | 15.7 / 24 | 334 / 390 |
| reduced | 3 | 20 | 470 | 0.005145 | 0.512 | 13.5 / 24 | 327 / 390 |
| **reduced** | **5** | **50** | **368** | **0.005141** | 0.522 | 13.7 / 24 | 322 / 390 |

Selection was by tuning log loss, so the full model keeps v1's twelve features (depth 5).
The seasonal and sustained-wind features ranked storm corridors slightly better (top-10
recall 0.55-0.56 against 0.52-0.54) but had worse log loss, so they were not adopted. The
tuning period holds only 24 storm-hour events, too few to decide on recall alone.

**Calibration** was fitted on the separate calibration period (2023-01-16 to 2023-11-28, 563
events). A small L2-regularised logistic calibrator on (margin, log(1+max(gust-40, 0)),
month sine, month cosine) was compared with sigmoid on the margin alone. The results below are
on the old test period. That period informed v2's design, so it is a **benchmark, not an
untouched test**:

| Model | Log loss | Brier | Average precision | Storm expected / observed | Quiet expected / observed |
|---|---|---|---|---|---|
| v1 (saved) | 0.006501 | 0.001041 | 0.0763 | 545 / 950 | 1347 / 1058 |
| v2 full + sigmoid | 0.006501 | 0.001041 | 0.0768 | 550 / 950 | 1341 / 1058 |
| **v2 full + seasonal calibrator** | **0.006473** | 0.001044 | **0.0781** | **929 / 950** | 1394 / 1058 |
| v2 reduced + sigmoid | 0.006611 | 0.001041 | 0.0732 | 601 / 950 | 1414 / 1058 |

The seasonal calibrator removes most of v1's storm under-prediction (545 to 929 of 950
expected) at the cost of over-predicting quiet hours (1394 against 1058). This matters for the
hybrid: storm hours are the hours where it changes which lines break.

**Inference** (`backend/ml_risk.py`):
- With complete ERA5-style route inputs, such as an archived replay from
  `python -m failure_model gb-export --v2 ...`, the full model and seasonal calibrator are used.
- Synthetic and UKV demos only have route gusts. They use the reduced model (gust max, p95,
  previous-3 h max, plus five static features) rather than missing-value paths.
- Hours without three hours of gust history are engineering-only.
- Archived exports are reused only when their `model_version` matches.

The result goes through the unchanged bounded hybrid. β = 0.5 and the multiplier is bounded to
0.5–2, so the ML can at most halve or double a line's engineering hazard. Each hour's expected
line failures are preserved and only redistributed between lines. A line's
damage draw in the cascade engine uses this tilted probability, so the model output decides
which lines break.

## Limitations

- Labels are distribution fault reports at candidate feeder points. The probabilities describe
  outage risk in a line's corridor and should be shown alongside the engineering line model,
  not called transmission-line failure probabilities.
- Training corridors are in SSEN territory (central southern England and northern Scotland).
  Other GB lines are geographic extrapolation; distribution density and tree cover differ
  by region.
- ERA5 at 0.5° smooths local peaks, and reanalysis replay is not an archived-forecast backtest.
- Report timestamps are provisional London-time conversions; onset is approximated by the
  first report.
- Independent Bernoulli draws per line ignore residual storm-level dependence. The reinforcement
  effect is an engineering assumption, not learned from these data.

## Reproduce

```bash
.venv/bin/python -m pip install -r requirements-model.txt
.venv/bin/python -m failure_model gb-dataset
.venv/bin/python -m failure_model gb-train
.venv/bin/python -m failure_model gb-train-v2
.venv/bin/python -m failure_model gb-export --v2 --start 2024-12-06T12:00Z --hours 60     --destination data/failure_model/exports/darragh-2024_gb-corridor-xgb-v2.json --name "Storm Darragh (ERA5 replay)"
.venv/bin/python scripts/compare_hybrid_decisions.py
.venv/bin/python scripts/audit_weather_sources.py
.venv/bin/python -m pytest -q tests/test_gb_line_model.py tests/test_failure_pipeline.py
```

These need the `dataRefininf` data archive extracted into `data/`, and network access to
the Open-Meteo archive API for replay windows outside the archive's cells.
