"""Gated XGBoost training, independent calibration and untouched storm evaluation."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import FEATURE_VERSION
from .common import FEATURES, now, sha256, write_json
from .evaluation import evaluate, metrics, plot_calibration
from .splits import assign_splits


class TrainingNotReady(ValueError):
    pass


def training_readiness(output):
    output = Path(output)
    report_path = output / "label_report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    feature_path = output / "feature_manifest.json"
    features = json.loads(feature_path.read_text()) if feature_path.exists() else {}
    reasons = []
    if not features.get("complete"):
        reasons.append("Feature materialization is incomplete")
    if not report.get("positive_rows"):
        reasons.append("No verified positive asset-hour labels")
    if not report.get("negative_rows"):
        reasons.append("No verified negative operating/reporting exposure")
    if report.get("rows") != features.get("rows"):
        reasons.append("Feature and label row counts differ; rebuild labels")
    for name, expected in report.get("evidence_sha256", {}).items():
        path = output / "evidence" / name
        if not path.exists() or sha256(path) != expected:
            reasons.append(f"Evidence changed since labelling: {name}")
    result = {"generated_utc": now(), "ready": not reasons, "reasons": reasons,
              "positive_rows": report.get("positive_rows", 0), "negative_rows": report.get("negative_rows", 0),
              "model_trained": False, "prediction_unit": "feeder", "feature_version": FEATURE_VERSION}
    write_json(output / "training_readiness.json", result)
    return result


def load_training_rows(output, negative_fraction=.02, seed=26):
    output = Path(output)
    if not 0 < negative_fraction <= 1:
        raise ValueError("Negative sampling fraction must be in (0,1]")
    assets = pd.read_parquet(output / "assets.parquet").set_index("asset_id")
    windows = pd.read_parquet(output / "storm_windows.parquet")
    primary = (assets.operator + ":" + assets.primary_nrn).to_dict()
    rng = np.random.default_rng(seed)
    frames = []
    for path in sorted((output / "labels").glob("*.parquet")):
        labels = pd.read_parquet(path)
        if labels.duplicated(["asset_id", "cutoff_utc"]).any():
            raise ValueError("Duplicate label keys")
        labels = labels[labels.label_verified.eq(True) & labels.at_risk_verified.eq(True) & labels.target.notna()].copy()
        if labels.empty:
            continue
        if not labels.target.isin([0, 1]).all():
            raise ValueError("Verified labels must be binary")
        split = assign_splits(labels, windows, primary)
        labels = labels.merge(split, on=["asset_id", "cutoff_utc"], validate="one_to_one")
        labels = labels[labels.split.isin(["train", "tune", "calibrate", "test"])].copy()
        # Geographic holdout is untouched at all stages and evaluated only in the test period.
        labels = labels[~labels.spatial_holdout | labels.split.eq("test")].copy()
        train_zero = labels.split.eq("train") & labels.target.eq(0)
        selected = ~train_zero | (rng.random(len(labels)) < negative_fraction)
        labels = labels[selected].copy()
        labels["sampling_weight"] = np.where(labels.split.eq("train") & labels.target.eq(0), 1 / negative_fraction, 1.)
        if labels.empty:
            continue
        features = pd.read_parquet(output / "features" / path.name)
        if features.duplicated(["asset_id", "cutoff_utc"]).any():
            raise ValueError("Duplicate feature keys")
        joined = labels.merge(features, on=["asset_id", "cutoff_utc"], validate="one_to_one", how="left", indicator=True)
        if joined._merge.ne("both").any() or joined.gust_route_max_lag_1h_kmh.isna().any():
            raise ValueError("Eligible labels lack complete route weather or lag history")
        frames.append(joined.drop(columns="_merge"))
    if not frames:
        raise TrainingNotReady("No eligible rows remain after verified exposure, purges and holdouts")
    result = pd.concat(frames, ignore_index=True)
    result["operator"] = result.asset_id.map(assets.operator)
    return result


def train_frames(frame, destination, seed=26, min_events=10, target="any_unplanned"):
    """Used with verified production rows; tests use explicitly synthetic fixtures."""
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from xgboost import XGBClassifier

    if target not in ("any_unplanned", "wind_weather"):
        raise ValueError("Unknown prediction target")
    destination = Path(destination)
    if (destination / "model.ubj").exists():
        raise ValueError("Model versions are immutable; choose a new model directory")
    if not frame.at_risk_verified.eq(True).all() or not frame.label_verified.eq(True).all():
        raise TrainingNotReady("Training requires verified operating exposure and labels")
    if frame.duplicated(["asset_id", "cutoff_utc"]).any():
        raise ValueError("Duplicate asset-hour rows")
    for name in ["train", "tune", "calibrate", "test"]:
        subset = frame[frame.split.eq(name) & ~frame.spatial_holdout]
        if subset.target.eq(1).sum() < min_events or subset.target.eq(0).sum() < min_events:
            raise TrainingNotReady(f"{name}: insufficient verified positive/negative outcomes (need {min_events} each)")
    periods = {name: frame[frame.split.eq(name) & ~frame.spatial_holdout] for name in ["train", "tune", "calibrate", "test"]}
    for earlier, later in zip(["train", "tune", "calibrate"], ["tune", "calibrate", "test"]):
        if periods[earlier].cutoff_utc.max() >= periods[later].cutoff_utc.min():
            raise ValueError("Training, tuning, calibration and test periods must be chronological")
        if set(periods[earlier].storm_id) & set(periods[later].storm_id):
            raise ValueError("Storm groups cannot cross partitions")
    names = list(periods)
    for i, earlier in enumerate(names):
        for later in names[i + 1:]:
            if set(periods[earlier].storm_id) & set(periods[later].storm_id):
                raise ValueError("Storm groups cannot cross any partitions")
    for name in ["tune", "calibrate", "test"]:
        if not periods[name].sampling_weight.eq(1).all():
            raise ValueError("Tuning/calibration/testing must retain full exposure")
    tr, va, ca, te = [periods[name] for name in ["train", "tune", "calibrate", "test"]]
    history = float(np.average(tr.target.astype(float), weights=tr.sampling_weight))
    baseline = make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                             LogisticRegression(max_iter=1000, C=1., random_state=seed))
    wind_features = ["gust_route_max_lag_1h_kmh", "gust_max_prev_24h_kmh", "overhead_length_km", "voltage_kv"]
    baseline.fit(tr[wind_features], tr.target.astype(int), logisticregression__sample_weight=tr.sampling_weight)
    candidates, best, score = [], None, float("inf")
    for depth, child_weight in [(2, 5), (3, 5), (3, 20), (4, 20)]:
        model = XGBClassifier(objective="binary:logistic", tree_method="hist", max_depth=depth,
                              min_child_weight=child_weight, learning_rate=.05, n_estimators=500,
                              reg_lambda=5., subsample=.8, colsample_bytree=.9, n_jobs=4,
                              early_stopping_rounds=30, eval_metric="logloss", random_state=seed)
        model.fit(tr[FEATURES], tr.target.astype(int), sample_weight=tr.sampling_weight,
                  eval_set=[(va[FEATURES], va.target.astype(int))], verbose=False)
        loss = float(log_loss(va.target.astype(int), model.predict_proba(va[FEATURES])[:, 1], labels=[0, 1]))
        candidates.append({"max_depth": depth, "min_child_weight": child_weight, "tuning_log_loss": loss,
                           "best_iteration": model.best_iteration})
        if loss < score:
            best, score = model, loss
    # Sigmoid calibration uses only the separate, representative calibration period.
    calibrator = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
    calibrator.fit(best.predict(ca[FEATURES], output_margin=True).reshape(-1, 1), ca.target.astype(int))
    destination.mkdir(parents=True, exist_ok=True)
    best.save_model(destination / "model.ubj")
    calibration = {"method": "sigmoid_on_margin", "coefficient": float(calibrator.coef_[0, 0]),
                   "intercept": float(calibrator.intercept_[0]), "period_start": str(ca.cutoff_utc.min()),
                   "period_end": str(ca.cutoff_utc.max()), "events": int(ca.target.sum())}
    write_json(destination / "calibration.json", calibration)
    previously_failed = set(tr.loc[tr.target.eq(1), "asset_id"])
    tests = frame[frame.split.eq("test")].copy()
    tests["previously_failed_in_training"] = tests.asset_id.isin(previously_failed)
    tests["probability"] = calibrator.predict_proba(best.predict(tests[FEATURES], output_margin=True).reshape(-1, 1))[:, 1]
    tests["uncalibrated_probability"] = best.predict_proba(tests[FEATURES])[:, 1]
    tests.to_parquet(destination / "held_out_predictions.parquet", index=False, compression="zstd")
    reports = {}
    for name, subset in [("temporal", tests[~tests.spatial_holdout]), ("geographic", tests[tests.spatial_holdout])]:
        if not len(subset):
            continue
        reports[name] = evaluate(subset, destination / f"{name}_evaluation.json", seed=seed)
        plot_calibration(reports[name], destination / f"{name}_calibration.png")
        reports[name]["historical_rate_baseline"] = metrics(subset.target, np.full(len(subset), history))
        reports[name]["wind_logistic_baseline"] = metrics(subset.target, baseline.predict_proba(subset[wind_features])[:, 1])
        write_json(destination / f"{name}_evaluation.json", reports[name])
    result = {"created_utc": now(), "feature_version": FEATURE_VERSION, "features": FEATURES,
              "prediction_unit": "feeder", "prediction_target": target,
              "horizon_hours": 1, "mode": "historical_reanalysis_replay",
              "training_cutoff": str(tr.cutoff_utc.max() + pd.Timedelta(hours=1)),
              "calibration_cutoff": str(ca.cutoff_utc.max() + pd.Timedelta(hours=1)),
              "seed": seed, "candidate_models": candidates, "best_tuning_log_loss": score,
              "calibration": calibration, "historical_rate": history,
              "training_assets": sorted(tr.asset_id.unique()),
              "rows_by_split": frame.groupby("split").size().to_dict(),
              "model_sha256": sha256(destination / "model.ubj"),
              "calibration_sha256": sha256(destination / "calibration.json"),
              "limitations": ["Feeder interruptions are not physical line-section failures",
                              "Reanalysis replay is not a live forecast validation",
                              "Current asset/woodland snapshots require historical-proxy sensitivity checks"]}
    write_json(destination / "model_manifest.json", result)
    return result


def train(output, destination, negative_fraction=.02, seed=26):
    ready = training_readiness(output)
    if not ready["ready"]:
        raise TrainingNotReady("; ".join(ready["reasons"]))
    rows = load_training_rows(output, negative_fraction, seed)
    output = Path(output)
    label_report = json.loads((output / "label_report.json").read_text())
    result = train_frames(rows, destination, seed, target=label_report["target"])
    result['dataset_provenance'] = {
        'directory': str(output), 'negative_sampling_fraction': negative_fraction,
        'feature_manifest_sha256': sha256(output / 'feature_manifest.json'),
        'label_report_sha256': sha256(output / 'label_report.json'),
        'evidence_sha256': json.loads((output / 'label_report.json').read_text())['evidence_sha256']}
    write_json(Path(destination) / 'model_manifest.json', result)
    ready.update(model_trained=True, model_manifest=str(Path(destination) / "model_manifest.json"))
    write_json(Path(output) / "training_readiness.json", ready)
    return result
