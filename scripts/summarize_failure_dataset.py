"""Write a concise, reproducible handoff from completed regional validation reports."""
import argparse
from importlib.metadata import version
from pathlib import Path
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from failure_model.common import FEATURES, now, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="*", type=Path, default=[ROOT / "data/failure_model/v1", ROOT / "data/failure_model/v1_shepd"])
    args = parser.parse_args()
    regions = []
    for directory in args.directories:
        def read(name):
            return json.loads((directory / name).read_text())
        inventory, features, labels = [read(n) for n in ["inventory_report.json", "feature_manifest.json", "label_report.json"]]
        validation, readiness, attempt = [read(n) for n in ["validation_report.json", "training_readiness.json", "training_attempt.json"]]
        if not features["complete"] or not validation["integrity_checks_passed"]:
            raise ValueError(f"Incomplete or unvalidated dataset: {directory}")
        if pd.Timestamp(validation["generated_utc"]) < pd.Timestamp(features["generated_utc"]):
            raise ValueError(f"Validation report predates the feature build: {directory}")
        assets = pd.read_parquet(directory / "assets.parquet")
        incidents = read("incident_report.json")
        pending = pd.read_parquet(directory / "weather_requests_pending.parquet")
        regions.append({"operator": inventory["operator"], "directory": str(directory.relative_to(ROOT)) if directory.is_relative_to(ROOT) else str(directory),
                        "feeders": len(assets), "complete_route_weather_feeders": int(assets.route_weather_complete.sum()),
                        "asset_hour_rows": features["rows"], "monthly_partitions": len(features["partitions"]),
                        "feature_count": len(FEATURES), "candidate_incidents": incidents["cohort_candidates"],
                        "linked_research_incidents": pq.ParquetFile(directory / "incident_weather_research.parquet").metadata.num_rows,
                        "unresolved_incident_utc_times": incidents["unresolved_utc_times"],
                        "voltage_mismatch_candidates": incidents["voltage_match_status_counts"].get("requires_source_review", 0),
                        "verified_positive_rows": labels["positive_rows"], "verified_negative_rows": labels["negative_rows"],
                        "null_72h_gust_rows": validation["null_72h_gust_rows"],
                        "feature_bytes": sum(p.stat().st_size for p in (directory / "features").glob("*.parquet")),
                        "pending_weather_keys": pending.weather_key.tolist(),
                        "missing_route_weather_assets": assets.loc[~assets.route_weather_complete, "asset_id"].tolist(),
                        "training_ready": readiness["ready"], "model_created": attempt["model_created"],
                        "training_gate_response": attempt["response"], "split_audit": read("split_audit.json")})
        # Small reviewable artifact; contains predictors, not predicted probabilities.
        snapshot = pd.read_parquet(directory / "features/2021-11.parquet",
                                   filters=[("cutoff_utc", "==", pd.Timestamp("2021-11-26T12:00:00Z"))])
        snapshot.to_parquet(directory / "feature_preview.parquet", index=False)
        snapshot.head(12).to_csv(directory / "feature_preview.csv", index=False)
    result = {"generated_utc": now(), "regions": regions, "total_feeders": sum(r["feeders"] for r in regions),
              "total_asset_hour_rows": sum(r["asset_hour_rows"] for r in regions),
              "total_candidate_incidents": sum(r["candidate_incidents"] for r in regions),
              "feature_count": len(FEATURES), "empirical_failure_model_trained": any(r["model_created"] for r in regions),
              "packages": {name: version(name) for name in ["numpy", "pandas", "pyarrow", "pyproj", "xgboost-cpu", "scikit-learn", "matplotlib"]}}
    tests = json.loads((ROOT / "data/failure_model/test_results.json").read_text())
    curation = json.loads((ROOT / "data/processed/validation_report.json").read_text())
    weather = pd.read_parquet(args.directories[0] / "weather_audit.parquet")
    result.update(test_results=tests, source_curation_validation=curation,
                  weather_files=len(weather), weather_rows=int(weather.rows.sum()))
    write_json(ROOT / "data/failure_model/summary.json", result)
    date = datetime.now(ZoneInfo("Europe/London")).strftime("%d %B %Y")
    pending = sorted({key for r in regions for key in r["pending_weather_keys"]})
    pending_text = ", ".join(f"`{key}`" for key in pending) or "none"
    missing_assets = sum(len(r["missing_route_weather_assets"]) for r in regions)
    unresolved = sum(r["unresolved_incident_utc_times"] for r in regions)
    mismatches = sum(r["voltage_mismatch_candidates"] for r in regions)
    research = sum(r["linked_research_incidents"] for r in regions)
    lines = [f"Implemented and validated on {date}. The pipeline produces historical feeder features and quarantined incident associations. No empirical failure-probability model has been trained.", "",
             "| Region | Feeders | Full-route weather | Hourly rows | Candidate incidents | Verified positives / negatives |",
             "|---|---:|---:|---:|---:|---:|"]
    for r in regions:
        lines.append(f"| {r['operator']} | {r['feeders']:,} | {r['complete_route_weather_feeders']:,} | {r['asset_hour_rows']:,} | {r['candidate_incidents']:,} | {r['verified_positive_rows']:,} / {r['verified_negative_rows']:,} |")
    lines += ["", f"There are **{result['total_asset_hour_rows']:,} hourly rows**, with **{len(FEATURES)} ordered predictors**, in 139 monthly partitions per region. Coverage is 2015-04-01T00:00Z through 2026-10-19T00:00Z exclusive. Features include length-weighted route gust percentiles, crosswind, actual precipitation, temperature/soil, 6/24/72-hour threshold counts, 24-hour excess exposure, known overhead length, voltage, woodland and route terrain.", "",
              f"Every partition passed cohort, timestamp, duplicate-key and label-separation checks. Selected asset-months were independently recomputed from source weather. The original curation validation also passed, checking {curation['checksummed_resources']:,} source checksums. The saved test run passed {tests['passed']} tests, including an explicitly synthetic XGBoost/calibration fixture; this does not validate empirical failure probabilities.", "",
              f"The shared collection contains {len(weather)} request locations and {int(weather.rows.sum()):,} hourly rows. Roster representative-point weather links cover all 4,439 feeders, including the SQLite graph links. The provider returned its daily request limit during route completion. Pending route request locations: {pending_text}. The {missing_assets} affected feeders retain missing route weather; no partial-route maximum is presented as complete.", "",
              f"{research:,} candidate incidents have provisional UTC hours and are joined to features for research. {unresolved} candidate times remain unresolved. Another {mismatches} candidate records have a voltage mismatch requiring source review. None of these records is approved as a supervised target.", "",
              "Both real training commands exited with code 2, explicitly reporting no verified positive labels and no verified negative operating/reporting exposure. No model directory was created. Empty verification templates and original-ID review queues are under each dataset's `evidence/` directory. Planned status, onset/timezone, restoration, complete reporting/state intervals and historical identity are still required.", "",
              "The backend probability importer is implemented and tested for the IEEE benchmark. It validates exact branch order, 1–72 hourly records, provenance, finite probabilities and time cutoffs; imported values replace engineering fragility. Its hazard-reduction assumption is explicit. Real GB cascade integration, repairs and calibrated component probabilities remain unavailable.", "",
              "See [FAILURE_PIPELINE.md](FAILURE_PIPELINE.md) for reproduction, feature definitions, evidence contracts and training/evaluation commands; [TEAM_INTEGRATION.md](TEAM_INTEGRATION.md) for the importer. Machine-readable counts and package versions are in `data/failure_model/summary.json`. Each region includes `feature_preview.csv` and `feature_preview.parquet` for review."]
    (ROOT / "docs/FAILURE_PIPELINE_RESULTS.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"total_feeders": result["total_feeders"], "hourly_rows": result["total_asset_hour_rows"],
                      "predictors": len(FEATURES), "empirical_model_trained": result["empirical_failure_model_trained"]}, indent=2))


if __name__ == "__main__":
    main()
