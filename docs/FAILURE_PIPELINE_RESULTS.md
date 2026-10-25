Implemented and validated on 25 October 2026. The pipeline produces historical feeder features and quarantined incident associations. No empirical failure-probability model has been trained.

| Region | Feeders | Full-route weather | Hourly rows | Candidate incidents | Verified positives / negatives |
|---|---:|---:|---:|---:|---:|
| SEPD | 1,050 | 1,050 | 106,318,800 | 23,439 | 0 / 0 |
| SHEPD | 825 | 822 | 83,536,200 | 25,902 | 0 / 0 |

There are **189,855,000 hourly rows**, with **49 ordered predictors**, in 139 monthly partitions per region. Coverage is 2015-04-01T00:00Z through 2026-10-19T00:00Z exclusive. Features include length-weighted route gust percentiles, crosswind, actual precipitation, temperature/soil, 6/24/72-hour threshold counts, 24-hour excess exposure, known overhead length, voltage, woodland and route terrain.

Every partition passed cohort, timestamp, duplicate-key and label-separation checks. Selected asset-months were independently recomputed from source weather. The original curation validation also passed, checking 387 source checksums. The saved test run passed 76 tests, including an explicitly synthetic XGBoost/calibration fixture; this does not validate empirical failure probabilities.

The shared collection contains 87 request locations and 8,809,272 hourly rows. Roster representative-point weather links cover all 4,439 feeders, including the SQLite graph links. The provider returned its daily request limit during route completion. Pending route request locations: `58.50_-7.00`, `60.50_-0.50`. The 3 affected feeders retain missing route weather; no partial-route maximum is presented as complete.

49,338 candidate incidents have provisional UTC hours and are joined to features for research. 3 candidate times remain unresolved. Another 22 candidate records have a voltage mismatch requiring source review. None of these records is approved as a supervised target.

Both real training commands exited with code 2, explicitly reporting no verified positive labels and no verified negative operating/reporting exposure. No model directory was created. Empty verification templates and original-ID review queues are under each dataset's `evidence/` directory. Planned status, onset/timezone, restoration, complete reporting/state intervals and historical identity are still required.

The backend probability importer is implemented and tested for the IEEE benchmark. It validates exact branch order, 1–72 hourly records, provenance, finite probabilities and time cutoffs; imported values replace engineering fragility. Its hazard-reduction assumption is explicit. Real GB cascade integration, repairs and calibrated component probabilities remain unavailable.

See [FAILURE_PIPELINE.md](FAILURE_PIPELINE.md) for reproduction, feature definitions, evidence contracts and training/evaluation commands; [TEAM_INTEGRATION.md](TEAM_INTEGRATION.md) for the importer. Machine-readable counts and package versions are in `data/failure_model/summary.json`. Each region includes `feature_preview.csv` and `feature_preview.parquet` for review.
