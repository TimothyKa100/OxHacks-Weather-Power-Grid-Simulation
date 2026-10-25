# IEEE14 model probability imports

The implemented importer supports the **IEEE 14-bus simulation only**. GB assets are available through `/api/grid`; SSEN feeder predictions require a verified electrical mapping before they can drive a real-network cascade.

Retrieve branch IDs in their exact order from `GET /api/network?name=ieee14`. `name=gb` returns the separate illustrative GB network from the storm simulator; the strict probability importer still accepts IEEE14 branch identities only.

Submit to `POST /api/storm-plan`:

```json
{
  "scenario": "imported",
  "weather": {
    "network": "ieee14",
    "prediction_unit": "branch",
    "source": "Provider / model provenance",
    "mode": "forecast",
    "branch_ids": ["all 20 IDs in API order"],
    "prediction": {
      "model_version": "your-model-version",
      "training_cutoff": "2025-12-31T00:00:00Z",
      "issued_at": "2026-10-24T12:00:00Z"
    },
    "hours": [
      {
        "timestamp": "2026-10-24T13:00:00Z",
        "gust_kmh": ["20 finite nonnegative numeric values"],
        "rain_mm_h": 2.0,
        "failure_probability": ["20 numeric probabilities in [0,1]"]
      }
    ]
  },
  "seed": 26,
  "samples": 32,
  "budget": 5
}
```

The quoted array descriptions are placeholders; actual requests need numeric arrays and the exact API IDs. Modes are `forecast`, `historical_replay` or `synthetic_test`; provenance is preserved. No imported source is automatically endorsed as calibrated. Probabilities mean a new direct weather failure during `[timestamp,timestamp+1h)`, conditional on operating at its start. All hours must be consecutive aligned timezone-aware records, with 1–72 hours. Training cutoff must precede prediction cutoffs; forecast issue time must also precede them. `training_cutoff` must include the last data used for tuning/calibration as applicable, not just the tree-fitting subset.

The same `weather` field is accepted by `POST /api/experiment`. Standard seed/sample/budget bounds and the one-experiment lock apply. Unsupported crew/repair controls are rejected because repairs are not implemented. Imported probabilities bypass fragility completely; gust/rain are retained as context. Reinforcement assumes a 0.25 hazard multiplier and 30% higher illustrative ratings. Outages persist for the experiment.

Responses retain imported source/model/hour metadata and distinguish hourly input probabilities from whole-event weather/cascade frequencies. `direct_weather_failure_frequency` reports direct-weather Monte Carlo frequency over the entire experiment; `line_failure_probability` includes cascade removals over the entire experiment. Neither is another calibrated hourly model output.

The storm dashboard is available at `/#storm`; the atlas remains at `/` and the original benchmark dashboard at `/#demo`. GB storm planning uses its own weather contract below. Feeder predictions are not automatically assigned to benchmark or transmission branches.


# GB storm planning and weather imports

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
