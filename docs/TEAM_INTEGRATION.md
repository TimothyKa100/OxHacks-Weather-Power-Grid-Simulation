> Current specification: [MODEL_SPEC.md](MODEL_SPEC.md). This document retains earlier model/source details. Raw corridor exports are still display-only; the new explicit paired hybrid mode can affect simulated line failures. No unpublished transmission training results are claimed.

# Team integration: atlas, failure predictions, decision engine

## Map and network teammate

The GB map now runs on the GB geographic network inherited from `feature/gb-storm-map`.
The OSM builder clusters endpoints, merges parallel ways and contracts series chains;
this is an approximate backbone, not a utility-validated electrical model. Reactances,
ratings, demand allocation and generator dispatch are assumptions. IEEE case14 remains
available through `/api/experiment` for independent physics regression tests.

Before making regional reliability claims, manually check line-to-substation
connections, crossings, voltage-changing transformers, regional boundary supplies
and demand mapping. A connected graph and balanced DC solution do not establish
that its electrical topology matches the operational grid.

The primary map endpoint is `/api/storm-plan`; its custom-plan replay descriptor
preserves the completed event and random draws. Additional real/ML weather exposures
can be imported via the map using the GB branch order below. See
[GB integration details](GB_INTEGRATION.md).

## ML teammate: one-hour operating-asset probabilities

The GB line-corridor XGBoost model (`python -m failure_model gb-export`) uses this hourly format, but its probabilities are **area-level corridor
outage risk** learned from SSEN distribution faults, not verified transmission-line
failures. They are shown on the map as a separate corridor-risk layer, and the importer refuses them
as line-failure inputs. See [GB_LINE_MODEL.md](GB_LINE_MODEL.md) for the target, data, split and results.

Use the weather import/API payload from MULTIDAY.md. Optionally supply predictions:

- `branch_ids`: every branch ID in exactly `/api/network` order (L01–L20 for IEEE14; use `GET /api/network?name=gb` for the GB map).
- `prediction`: string `model_version`, ISO timezone-aware `training_cutoff` and
  `issued_at`. Training cutoff must not follow issue time; issue time must not follow
  the first replay interval's start.
- Every hourly row: `failure_probability`, an array in that same order, finite in
  [0,1]. Supply every hour or none. This is the probability of a weather-caused asset
  outage during that hour conditional on operating at interval start, not customer
  interruption probability, cumulative storm probability or an uncalibrated score.

Gust/rain remain required because repair access depends on wind. Supplied probabilities
replace the engineering weather-failure formula; they are not multiplied by it.
Baseline/hardened probabilities are related by `p_hard = 1−(1−p)^0.25`, an explicit
assumed effect. Cascading overloads are still calculated by the electrical model.
External prediction provenance is preserved in the weather export. Manifest fields
cannot verify whether model features contain hindsight: the ML team owns that check.

The same weather/predictions are used for training *investment selection* and
held-out simulation draws. This simulator training bank is not ML model training.
It does not validate the underlying ML probabilities on real incidents.

## Evaluate predictions using real labels

`scripts/evaluate_predictions.py` scores submitted predictions against observed
asset-hour labels. It outputs Brier score, log loss, calibration bins and Brier skill
relative to a constant failure probability estimated using training data.

```
.venv/bin/python scripts/evaluate_predictions.py predictions.csv model_manifest.json evaluation.json
```

CSV columns: `storm_id,timestamp,asset_id,probability,failed` (failed is 0/1).
Manifest example, with placeholders that must be replaced:

```json
{
  "model_version":"your-model-version",
  "training_cutoff":"2025-01-01T00:00:00Z",
  "training_storms":["training-event-id"],
  "baseline_failure_probability":0.01,
  "labels_source":"Actual incident source and non-event sampling method"
}
```

Rows must follow the cutoff, evaluation storm IDs must be excluded from training,
and asset-hour identities must be unique. Reserve whole storms for evaluation,
including tuning/calibration. The baseline shown is an example, not an estimated
UK failure frequency. Do not count unreported incidents as confirmed non-events or
already failed hours as new exposure. Avoid case-control downsampling without
correcting probabilities/weights to deployment prevalence. Customer outage reports
without asset attribution cannot validate line-level predictions.

Useful data starting points: ERA5 for weather replay, archived forecasts for genuine
forecast backtests, Elexon/NESO for generation/demand, and operator incident/storm
reports for labels. Historical weather alone is insufficient for supervised failure
training. No labels, model fitting, regional mapping or predictive accuracy is claimed
by this prototype.
