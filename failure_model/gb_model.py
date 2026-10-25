"""Train, calibrate, evaluate and export the GB line-corridor XGBoost outage-risk model.

Chronological split of the line-hour rows: the first 75% of hours is used for model
development (fit 0-60%, early stopping/tuning 60-67.5%, calibration 67.5-75%) and the
final 25% is an untouched test period. Each boundary moves to the start of any storm
window it would cut, and rows within 25 h (24 h lookback + 1 h horizon) of a boundary
are purged. No class weights or resampling: rows keep the real failure frequency.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .common import ROOT, now, sha256, write_json
from .evaluation import metrics
from .gb_lines import (ASSET_FEATURES, DERIVED, FEATURES, OUTPUT, VEGETATION, WEATHER_FEATURES,
                       line_features, load_network, route_links)

MODEL_VERSION = "gb-corridor-xgb-v1"
MODEL_DIR = ROOT / "data/failure_model/models" / MODEL_VERSION
FRACTIONS = {"fit": .60, "tune": .675, "calibrate": .75}
PURGE = pd.Timedelta(hours=25)
FEATURE_SETS = {"spec": FEATURES, "spec+vegetation": FEATURES + VEGETATION, "spec+derived": FEATURES + DERIVED}
# Stronger wind cannot lower risk with everything else fixed (physical prior).
INCREASING = {"gust_max_kmh", "gust_p95_kmh", "gust_prev_3h_max_kmh", "crosswind_max_kmh"}
CONFIGS = [(2, 5), (3, 20), (4, 20), (5, 50)]
RELIABILITY_EDGES = [0, .0001, .0003, .001, .003, .01, .03, .1, .3, 1.000001]


def boundaries(hours, windows):
    """Split instants at fixed fractions of the hour range, moved out of storm windows."""
    start, end = hours.min(), hours.max() + pd.Timedelta(hours=1)
    result = {}
    for name, fraction in FRACTIONS.items():
        b = (start + (end - start) * fraction).floor("h")
        inside = windows[(windows.start_utc <= b) & (windows.end_utc > b)]
        if len(inside):
            b = inside.start_utc.min()
        result[name] = b
    return result


def assign_split(rows, bounds):
    t = rows.timestamp_utc
    split = np.select([t < bounds["fit"], t < bounds["tune"], t < bounds["calibrate"]],
                      ["fit", "tune", "calibrate"], "test").astype(object)
    for b in bounds.values():
        split[((t >= b - PURGE) & (t < b + PURGE)).to_numpy()] = "purged"
    return split


def _model(depth, child_weight, features, seed):
    from xgboost import XGBClassifier
    constraints = "(" + ",".join("1" if f in INCREASING else "0" for f in features) + ")"
    return XGBClassifier(objective="binary:logistic", tree_method="hist", max_depth=depth,
                         min_child_weight=child_weight, learning_rate=.05, n_estimators=1500,
                         reg_lambda=5., subsample=.8, colsample_bytree=.9, n_jobs=-1,
                         monotone_constraints=constraints, early_stopping_rounds=50,
                         eval_metric="logloss", random_state=seed)


def fit_candidate(parts, features, depth, child_weight, seed):
    from sklearn.metrics import log_loss
    fit, tune = parts["fit"], parts["tune"]
    model = _model(depth, child_weight, features, seed)
    model.fit(fit[features], fit.failed_next_hour, eval_set=[(tune[features], tune.failed_next_hour)], verbose=False)
    loss = float(log_loss(tune.failed_next_hour, model.predict_proba(tune[features])[:, 1], labels=[0, 1]))
    return model, {"features": features, "max_depth": depth, "min_child_weight": child_weight,
                   "best_iteration": int(model.best_iteration), "tuning_log_loss": loss}


def calibrated(model, calibration, frame):
    from scipy.special import expit
    margin = model.predict(frame, output_margin=True)
    return expit(calibration["coefficient"] * margin + calibration["intercept"])


def reliability(y, p):
    bins = np.digitize(p, RELIABILITY_EDGES[1:-1])
    out = []
    for i in range(len(RELIABILITY_EDGES) - 1):
        mask = bins == i
        if mask.any():
            out.append({"lower": RELIABILITY_EDGES[i], "upper": min(RELIABILITY_EDGES[i + 1], 1.), "rows": int(mask.sum()),
                        "mean_probability": float(p[mask].mean()), "observed_frequency": float(y[mask].mean())})
    return out


def evaluate(frame, probability, history_rate, seed=26, samples=1000):
    y = frame.failed_next_hour.to_numpy()
    report = metrics(y, probability)
    base = float(((history_rate - y) ** 2).mean())
    report["brier_skill_vs_training_rate"] = 1 - report["brier_score"] / base
    report["reliability"] = reliability(y, probability)
    storm = ~frame.storm_id.str.startswith("quiet").to_numpy()
    report["storm_hours"] = metrics(y[storm], probability[storm])
    report["quiet_hours"] = metrics(y[~storm], probability[~storm])
    report["by_kv"] = {str(int(k)): metrics(y[m], probability[m])
                       for k in sorted(frame.kv.unique()) for m in [(frame.kv == k).to_numpy()]}
    g = frame.assign(p=probability, err=(probability - y) ** 2).groupby("storm_id")
    totals = g.agg(rows=("p", "size"), events=("failed_next_hour", "sum"), expected_events=("p", "sum"), brier_sum=("err", "sum"))
    report["storm_totals"] = (totals[~totals.index.str.startswith("quiet")].reset_index()
                              .sort_values("events", ascending=False).to_dict("records"))
    rng = np.random.default_rng(seed)
    counts, sums = totals.rows.to_numpy(), totals.brier_sum.to_numpy()
    scores = [sums[s].sum() / counts[s].sum() for s in (rng.integers(0, len(totals), len(totals)) for _ in range(samples))]
    report["brier_group_bootstrap_95_interval"] = np.quantile(scores, [.025, .975]).tolist()
    return report


def plot_reliability(report, destination):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 5))
    for name, r in report.items():
        bins = r["reliability"]
        ax.plot([b["mean_probability"] for b in bins], [b["observed_frequency"] for b in bins], "o-", label=name)
    ax.plot([1e-5, 1], [1e-5, 1], "--", color="gray", label="Perfect calibration")
    ax.set(xscale="log", yscale="log", xlabel="Predicted hourly corridor outage probability",
           ylabel="Observed hourly frequency", title="Test-period reliability (untouched final 25%)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(destination, dpi=160)
    plt.close(fig)


def train(rows_path=OUTPUT / "rows.parquet", destination=MODEL_DIR, seed=26):
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    destination = Path(destination)
    if (destination / "model.ubj").exists():
        raise ValueError("Model versions are immutable; choose a new model directory")
    rows = pd.read_parquet(rows_path)
    windows = pd.read_parquet(Path(rows_path).parent / "storm_windows.parquet")
    hours = pd.DatetimeIndex(rows.timestamp_utc.unique()).sort_values()
    bounds = boundaries(hours, windows)
    rows["split"] = assign_split(rows, bounds)
    parts = {name: rows[rows.split.eq(name)] for name in ["fit", "tune", "calibrate", "test"]}
    names = list(parts)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if parts[a].timestamp_utc.max() >= parts[b].timestamp_utc.min():
                raise ValueError("Partitions must be chronological")
            shared = (set(parts[a].storm_id) & set(parts[b].storm_id)) - {s for s in parts[a].storm_id if s.startswith("quiet")}
            if shared:
                raise ValueError(f"Storm windows cross partitions: {sorted(shared)[:5]}")

    # Which feature sets could be used for every GB branch at inference time?
    lines = pd.DataFrame(load_network())
    vegetation = pd.read_csv(Path(rows_path).parent / "lines.csv").set_index("asset_id").vegetation_fraction
    complete = {"spec": True, "spec+derived": True, "spec+vegetation": bool(vegetation.reindex(lines.id).notna().all())}

    candidates = []
    best_spec = None
    for depth, child in CONFIGS:
        model, info = fit_candidate(parts, FEATURE_SETS["spec"], depth, child, seed)
        info["feature_set"] = "spec"
        candidates.append(info)
        print(json.dumps({k: v for k, v in info.items() if k != "features"}), flush=True)
        if best_spec is None or info["tuning_log_loss"] < best_spec[1]["tuning_log_loss"]:
            best_spec = (model, info)
    depth, child = best_spec[1]["max_depth"], best_spec[1]["min_child_weight"]
    ablation = {"spec": best_spec}
    for name in ["spec+vegetation", "spec+derived"]:
        model, info = fit_candidate(parts, FEATURE_SETS[name], depth, child, seed)
        info["feature_set"] = name
        candidates.append(info)
        ablation[name] = (model, info)
        print(json.dumps({k: v for k, v in info.items() if k != "features"}), flush=True)
    deployable = {k: v for k, v in ablation.items() if complete[k]}
    chosen = min(deployable, key=lambda k: deployable[k][1]["tuning_log_loss"])
    model, info = ablation[chosen]
    features = info["features"]

    # Platt scaling on the model margin, fitted only on the separate calibration period.
    ca = parts["calibrate"]
    platt = LogisticRegression(C=1e6, max_iter=1000).fit(model.predict(ca[features], output_margin=True).reshape(-1, 1), ca.failed_next_hour)
    calibration = {"method": "sigmoid_on_margin", "coefficient": float(platt.coef_[0, 0]), "intercept": float(platt.intercept_[0]),
                   "period_start": str(ca.timestamp_utc.min()), "period_end": str(ca.timestamp_utc.max()),
                   "rows": len(ca), "events": int(ca.failed_next_hour.sum())}

    fit = parts["fit"]
    history_rate = float(fit.failed_next_hour.mean())
    wind_features = ["gust_max_kmh", "gust_prev_3h_max_kmh", "length_km", "kv"]
    baseline = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=1000))
    baseline.fit(fit[wind_features], fit.failed_next_hour)

    from .reference import fit as fit_reference
    reference_model = fit_reference(fit, ca)

    test = parts["test"].copy()
    test["probability"] = calibrated(model, calibration, test[features])
    test["uncalibrated_probability"] = model.predict_proba(test[features])[:, 1]
    report = {"calibrated_xgboost": evaluate(test, test.probability.to_numpy(), history_rate, seed),
              "uncalibrated_xgboost": evaluate(test, test.uncalibrated_probability.to_numpy(), history_rate, seed),
              "training_rate_baseline": metrics(test.failed_next_hour, np.full(len(test), history_rate)),
              "wind_logistic_baseline": metrics(test.failed_next_hour, baseline.predict_proba(test[wind_features])[:, 1])}
    for name, (other, other_info) in ablation.items():
        report[f"ablation_{name}_uncalibrated_test"] = metrics(test.failed_next_hour, other.predict_proba(test[other_info["features"]])[:, 1])

    destination.mkdir(parents=True, exist_ok=True)
    model.save_model(destination / "model.ubj")
    write_json(destination / "calibration.json", calibration)
    write_json(destination / "reference_model.json", reference_model)
    write_json(destination / "evaluation.json", report)
    plot_reliability({"calibrated XGBoost": report["calibrated_xgboost"],
                      "uncalibrated XGBoost": report["uncalibrated_xgboost"]}, destination / "reliability.png")
    test[["asset_id", "timestamp_utc", "storm_id", "kv", "failed_next_hour", "probability", "uncalibrated_probability"]].to_parquet(
        OUTPUT / "held_out_predictions.parquet", index=False, compression="zstd")
    importance = model.get_booster().get_score(importance_type="gain")
    manifest = {"model_version": destination.name, "created_utc": now(), "features": features, "feature_set": chosen,
                "feature_sets_complete_for_all_gb_branches": complete, "monotone_increasing": sorted(INCREASING & set(features)),
                "prediction_unit": "GB simulator branch corridor (5 km) x UTC hour",
                "target": "P(new weather-caused SSEN overhead-line fault report inside the corridor during [t,t+1h))",
                "interpretation": "Area-level outage risk; not a verified transmission-line failure probability",
                "gust_convention": "raw ERA5 10 m gust via Open-Meteo (preceding-hour maximum), 0.5-degree cells; no terrain adjustment",
                "split_boundaries_utc": {k: v.isoformat() for k, v in bounds.items()},
                "training_cutoff": bounds["calibrate"].isoformat(),
                "rows_by_split": pd.Series(rows.split).value_counts().to_dict(),
                "events_by_split": rows.groupby("split").failed_next_hour.sum().astype(int).to_dict(),
                "training_storms": sorted({s for name in ["fit", "tune", "calibrate"] for s in parts[name].storm_id if not s.startswith("quiet")}),
                "test_storms": sorted({s for s in test.storm_id if not s.startswith("quiet")}),
                "training_domain_lines": sorted(rows.asset_id.unique()),
                "training_rate": history_rate, "candidates": candidates, "chosen_config": {"max_depth": depth, "min_child_weight": child},
                "calibration": calibration, "feature_importance_gain": importance, "seed": seed,
                "model_sha256": sha256(destination / "model.ubj"), "calibration_sha256": sha256(destination / "calibration.json"),
                "reference_sha256": sha256(destination / "reference_model.json"),
                "dataset_report": json.loads((Path(rows_path).parent / "dataset_report.json").read_text()),
                "limitations": ["Labels are distribution fault reports located at candidate feeder points, not failed transmission assets",
                                "Training corridors lie in SSEN licence areas; other GB lines are geographic extrapolation",
                                "ERA5 0.5-degree reanalysis smooths peak gusts; replay uses reanalysis, not archived forecasts",
                                "Reinforcement effect (1-(1-p)^0.25) is an engineering assumption, not learned"]}
    write_json(destination / "model_manifest.json", manifest)
    return manifest, report


def load_model(directory=MODEL_DIR):
    from xgboost import XGBClassifier
    directory = Path(directory)
    manifest = json.loads((directory / "model_manifest.json").read_text())
    calibration_bytes=(directory / "calibration.json").read_bytes()
    import hashlib
    variants=[calibration_bytes,calibration_bytes.replace(b'\r\n',b'\n'),calibration_bytes.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')]
    valid_calibration=manifest['calibration_sha256'] in [hashlib.sha256(b).hexdigest() for b in variants]
    if sha256(directory / "model.ubj") != manifest["model_sha256"] or not valid_calibration:
        raise ValueError("Model or calibration checksum mismatch")
    model = XGBClassifier()
    model.load_model(directory / "model.ubj")
    return model, json.loads((directory / "calibration.json").read_text()), manifest


def export_profile(start, hours, destination, directory=MODEL_DIR, branch_ids=None, name=None, paired=False):
    """Weather/ML JSON for POST /api/storm-plan: one calibrated probability per branch and hour."""
    from .gb_weather import fetch_window, load
    model, calibration, manifest = load_model(directory)
    start = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
    if not 1 <= hours <= 72:
        raise ValueError("The importer accepts 1-72 hourly records")
    if start < pd.Timestamp(manifest["training_cutoff"]):
        raise ValueError("Replay must start after the model's training cutoff")
    reference=None
    if paired:
        from .reference import predict as reference_predict
        ref_path=Path(directory)/'reference_model.json'
        if not ref_path.exists() or not manifest.get('reference_sha256'):
            raise ValueError('This model has no fitted weather-aware reference. Retrain to a new version; no constant-rate substitute is permitted.')
        if sha256(ref_path)!=manifest['reference_sha256']: raise ValueError('Reference checksum mismatch')
        reference=json.loads(ref_path.read_text())
    lines = load_network()
    order = [line["id"] for line in lines]
    if branch_ids is not None and list(branch_ids) != order:
        raise ValueError("data/gb_network.json order differs from /api/network?name=gb")
    links = {line["id"]: route_links(line) for line in lines}
    keys = sorted({k for l in links.values() for k in l.weather_key})
    intervals = pd.date_range(start, periods=hours, freq="h")
    stamps = pd.date_range(start - pd.Timedelta(hours=24), intervals[-1] + pd.Timedelta(hours=1), freq="h")
    folder = fetch_window(keys, stamps[0].strftime("%Y-%m-%d"), stamps[-1].strftime("%Y-%m-%d"))
    weather = load(keys, stamps, folder)
    columns = {}
    for line in lines:
        f = line_features(links[line["id"]], weather, keys, stamps, intervals)
        frame = pd.DataFrame(f)
        for col in ASSET_FEATURES + DERIVED:
            frame[col] = float(line[col])
        if frame[WEATHER_FEATURES].isna().any().any():
            raise ValueError(f"{line['id']}: incomplete route weather for the replay window")
        risk = calibrated(model, calibration, frame[manifest["features"]].astype(np.float32))
        # Only overhead routes have a corridor exposure; cables, transformers and inferred links get none.
        columns[line["id"]] = (frame, risk if line["kind"] == "line" else np.zeros(len(risk)))
    profile = {
        "source": (f"GridGuard {manifest['model_version']}: calibrated XGBoost hourly outage risk in each line's 5 km corridor, "
                   "trained on SSEN NaFIRS weather-caused overhead-line faults with ERA5 (Open-Meteo) route weather. "
                   "Area-level proxy, not a verified transmission-line failure probability."),
        "mode": "historical_replay",
        "prediction_unit": "line_corridor_area_risk",
        "name": name,
        "branch_ids": order,
        "prediction": {"model_version": MODEL_VERSION, "training_cutoff": manifest["training_cutoff"],
                       "issued_at": intervals[0].isoformat(), "target": manifest["target"],
                       "gust_convention": manifest["gust_convention"],
                       "training_domain_lines": manifest["training_domain_lines"]},
        "hours": [{"timestamp": t.isoformat(),
                   "gust_kmh": [round(float(columns[i][0].gust_max_kmh.iloc[h]), 1) for i in order],
                   "rain_mm_h": [round(float(columns[i][0].rain_1h_mm.iloc[h]), 2) for i in order],
                   "failure_probability": [round(float(columns[i][1][h]), 6) for i in order]}
                  for h, t in enumerate(intervals)]}
    if paired:
        from backend.hybrid import validate_pair
        profile['prediction_unit']='paired_risk_modifier'
        profile['prediction'].update(reference_model_version=manifest['model_version']+'-weather-reference',
                                     target='corridor_outage',evidence_status='observed_labels')
        reference_columns={i:reference_predict(columns[i][0],reference) for i in order}
        profile['hours']=[{'timestamp':t.isoformat(),
            'full_probability':[float(columns[i][1][h]) for i in order],
            'reference_probability':[float(reference_columns[i][h]) for i in order]}
            for h,t in enumerate(intervals)]
        validate_pair(profile,order)
        destination=Path(destination); destination.parent.mkdir(parents=True,exist_ok=True)
        write_json(destination,profile)
        return profile
    from backend.weather import validate_weather
    # Same hourly contract as the importer; the area-risk marker keeps it out of simulations.
    validate_weather({**profile, "prediction_unit": None}, len(order))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(profile, separators=(",", ":")))
    return profile
