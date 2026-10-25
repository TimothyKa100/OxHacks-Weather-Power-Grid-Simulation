The implemented importer supports the **IEEE 14-bus simulation only**. GB assets are available through `/api/grid`; SSEN feeder predictions require a verified electrical mapping before they can drive a real-network cascade.

Retrieve branch IDs in their exact order from `GET /api/network?name=ieee14`. Requesting `name=gb` explicitly returns an error instead of returning the benchmark under that name.

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

The frontend importer and real-GB cascade simulator described in earlier notes are not present in this checkout. This implementation supplies the validated backend boundary, without assigning distribution interruption labels to unrelated benchmark or transmission branches.
