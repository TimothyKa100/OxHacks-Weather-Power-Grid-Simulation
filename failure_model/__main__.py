"""Run with python -m failure_model; commands keep evidence and predictions separate."""
import argparse
import json
from pathlib import Path
import sys

from .common import DEFAULT_OUTPUT, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--operator", choices=["SEPD", "SHEPD", "ALL"], default="SEPD")
    commands.add_parser("incidents")
    features = commands.add_parser("features")
    features.add_argument("--start", default="2015-04-01T00:00:00Z")
    features.add_argument("--end", default="2026-10-19T00:00:00Z", help="Exclusive UTC end")
    features.add_argument("--rebuild", action="store_true")
    commands.add_parser("storms")
    commands.add_parser("link-incidents")
    labels = commands.add_parser("labels")
    labels.add_argument("--target", choices=["any_unplanned", "wind_weather"], default="any_unplanned")
    commands.add_parser("readiness")
    commands.add_parser("validate")
    download = commands.add_parser("fetch-weather")
    download.add_argument("--include-roster", action="store_true")
    train = commands.add_parser("train")
    train.add_argument("--model-dir", type=Path, required=True)
    train.add_argument("--negative-fraction", type=float, default=.02)
    train.add_argument("--seed", type=int, default=26)
    predict = commands.add_parser("predict")
    predict.add_argument("--model-dir", type=Path, required=True)
    predict.add_argument("--features", type=Path, required=True)
    predict.add_argument("--issued-at", required=True)
    predict.add_argument("--destination", type=Path, required=True)
    commands.add_parser("gb-dataset", help="GB line-corridor rows from SSEN labels and ERA5 route weather")
    gb_train = commands.add_parser("gb-train", help="XGBoost on the first 75% of hours; test on the last 25%")
    gb_train.add_argument("--model-dir", type=Path)
    gb_export = commands.add_parser("gb-export", help="Weather/ML JSON for POST /api/storm-plan")
    gb_export.add_argument("--start", required=True, help="First UTC hour, e.g. 2024-12-06T12:00Z")
    gb_export.add_argument("--hours", type=int, default=60)
    gb_export.add_argument("--destination", type=Path, required=True)
    gb_export.add_argument("--model-dir", type=Path)
    gb_export.add_argument("--api", help="Running app URL; branch order is read from /api/network?name=gb")
    gb_export.add_argument("--name")
    args = parser.parse_args()
    output = args.output
    weather = ROOT / "data/processed/weather"
    if args.command == "inventory":
        from .inventory import build_inventory
        result = build_inventory(output, args.operator)
    elif args.command == "incidents":
        from .evidence import associate_incidents
        result = associate_incidents(output)
    elif args.command == "features":
        from .features import FeatureBuilder
        result = FeatureBuilder(output, weather).build(args.start, args.end, resume=not args.rebuild)
        result = {k: v for k, v in result.items() if k not in ["configuration", "partitions"]}
    elif args.command == "storms":
        from .splits import build_storm_windows
        result = {"weather_clusters": len(build_storm_windows(output, weather))}
    elif args.command == "link-incidents":
        from .evidence import link_incident_features
        result = link_incident_features(output)
    elif args.command == "labels":
        from .evidence import build_labels
        result = build_labels(output, args.target)
    elif args.command == "readiness":
        from .training import training_readiness
        result = training_readiness(output)
    elif args.command == "validate":
        from .validation import validate_dataset
        result = validate_dataset(output, weather)
    elif args.command == "fetch-weather":
        from .download import fetch_pending
        result = fetch_pending(output, args.include_roster)
    elif args.command == "gb-dataset":
        from .gb_lines import build_dataset
        result = {k: v for k, v in build_dataset().items() if k != "weather_cells"}
    elif args.command == "gb-train":
        from .gb_model import MODEL_DIR, train as train_gb
        manifest, report = train_gb(destination=args.model_dir or MODEL_DIR)
        result = {"model_version": manifest["model_version"], "feature_set": manifest["feature_set"],
                  "training_cutoff": manifest["training_cutoff"],
                  "test": {k: report["calibrated_xgboost"][k] for k in
                           ["rows", "events", "expected_events", "brier_score", "log_loss", "average_precision",
                            "brier_skill_vs_training_rate"]}}
    elif args.command == "gb-export":
        from .gb_model import MODEL_DIR, export_profile
        order = None
        if args.api:
            import requests
            order = [b["id"] for b in requests.get(args.api.rstrip("/") + "/api/network", params={"name": "gb"},
                                                   timeout=30).json()["branches"]]
        profile = export_profile(args.start, args.hours, args.destination, args.model_dir or MODEL_DIR, order, args.name)
        p = [x for h in profile["hours"] for x in h["failure_probability"]]
        result = {"destination": str(args.destination), "hours": len(profile["hours"]), "branches": len(profile["branch_ids"]),
                  "mean_probability": sum(p) / len(p), "max_probability": max(p)}
    elif args.command == "train":
        from .training import train, TrainingNotReady
        try:
            result = train(output, args.model_dir, args.negative_fraction, args.seed)
        except TrainingNotReady as error:
            print(json.dumps({"model_trained": False, "reason": str(error)}, indent=2))
            return 2
    else:
        import pandas as pd
        from .predict import predict
        result = predict(pd.read_parquet(args.features), args.model_dir, args.issued_at)
        args.destination.parent.mkdir(parents=True, exist_ok=True)
        result.to_parquet(args.destination, index=False)
        result = {"prediction_rows": len(result), "prediction_unit": "feeder"}
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
