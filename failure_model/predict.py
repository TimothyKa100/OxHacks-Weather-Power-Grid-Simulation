"""Inference keeps feeder outputs separate from physical branch simulation imports."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import FEATURE_VERSION
from .common import FEATURES, require_columns, sha256, utc


def predict(frame, model_directory, issued_at, mode="historical_reanalysis_replay"):
    from xgboost import XGBClassifier
    directory = Path(model_directory)
    require_columns(frame, ["asset_id", "cutoff_utc", *FEATURES], "inference features")
    manifest = json.loads((directory / "model_manifest.json").read_text())
    if manifest["feature_version"] != FEATURE_VERSION or manifest["features"] != FEATURES:
        raise ValueError("Model and inference feature contracts differ")
    if sha256(directory / "model.ubj") != manifest["model_sha256"]:
        raise ValueError("Model checksum mismatch")
    if sha256(directory / "calibration.json") != manifest["calibration_sha256"]:
        raise ValueError("Calibration checksum mismatch")
    if mode != "historical_reanalysis_replay":
        raise ValueError("This feature version is reanalysis replay; archived forecast features are required for operational inference")
    issued = utc(issued_at)
    if issued < utc(manifest["calibration_cutoff"]):
        raise ValueError("issued_at precedes calibration data cutoff")
    if frame.duplicated(["asset_id", "cutoff_utc"]).any() or frame.gust_route_max_lag_1h_kmh.isna().any():
        raise ValueError("Inference needs unique keys and complete route gust/history")
    if not isinstance(frame.cutoff_utc.dtype, pd.DatetimeTZDtype) or str(frame.cutoff_utc.dt.tz) != "UTC":
        raise ValueError("Prediction cutoffs must be timezone-aware UTC")
    if frame.cutoff_utc.isna().any() or frame.cutoff_utc.ne(frame.cutoff_utc.dt.floor("h")).any():
        raise ValueError("Prediction cutoffs must be non-null aligned hours")
    if frame.cutoff_utc.gt(issued).any():
        raise ValueError("Historical replay cutoffs must not follow issued_at")
    if "horizon_hours" in frame and not frame.horizon_hours.eq(1).all():
        raise ValueError("This model predicts one-hour intervals")
    if frame.cutoff_utc.lt(utc(manifest["calibration_cutoff"])).any():
        raise ValueError("Prediction cutoffs precede the fitted model/calibration data cutoff")
    model = XGBClassifier()
    model.load_model(directory / "model.ubj")
    calibration = json.loads((directory / "calibration.json").read_text())
    margin = model.predict(frame[FEATURES], output_margin=True)
    z = calibration["coefficient"] * margin + calibration["intercept"]
    from scipy.special import expit
    out = frame[["asset_id", "cutoff_utc"]].copy()
    out["failure_probability"] = expit(z)
    out["prediction_unit"] = "feeder"
    out["prediction_target"] = manifest["prediction_target"]
    out["horizon_hours"] = 1
    out["model_version"] = directory.name
    out["issued_at"] = issued
    out["training_cutoff"] = utc(manifest["training_cutoff"])
    out["calibration_cutoff"] = utc(manifest["calibration_cutoff"])
    out["feature_version"] = FEATURE_VERSION
    out["mode"] = mode
    out["previously_seen_asset"] = out.asset_id.isin(manifest["training_assets"])
    out["electrical_branch_mapping_verified"] = False
    return out
