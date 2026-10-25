"""Shared contracts and reproducibility helpers."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data/failure_model/v1"
LOOKBACK_HOURS = 72
HORIZON_HOURS = 1
FEATURES = [
    "gust_route_max_lag_1h_kmh", "gust_route_p95_lag_1h_kmh",
    "crosswind_route_max_lag_1h_kmh", "wind_speed_route_mean_lag_1h_kmh",
    "gust_max_prev_3h_kmh", "gust_max_prev_6h_kmh",
    "gust_max_prev_24h_kmh", "gust_max_prev_72h_kmh",
    "precipitation_prev_1h_mm", "precipitation_prev_24h_mm",
    "precipitation_prev_72h_mm", "temperature_route_mean_lag_1h_c",
    "temperature_min_prev_24h_c", "temperature_max_prev_24h_c",
    "soil_moisture_route_mean_lag_1h_m3_m3",
    "gust_ge_80_sample_hours_prev_24h", "gust_ge_100_sample_hours_prev_24h",
    "gust_ge_120_sample_hours_prev_24h", "gust_ge_80_sample_hours_prev_72h",
    "gust_excess_80_prev_24h_kmh_sample_hours",
    "overhead_length_km", "voltage_kv", "woodland_exposure_fraction",
    "terrain_min_m", "terrain_mean_m", "terrain_max_m",
    "month_sin", "month_cos", "hour_sin", "hour_cos",
]
for _threshold in (80, 100, 120):
    for _hours in (6, 24, 72):
        _name = f"gust_ge_{_threshold}_sample_hours_prev_{_hours}h"
        if _name not in FEATURES:
            FEATURES.append(_name)
    _name = f"gust_excess_{_threshold}_prev_24h_kmh_sample_hours"
    if _name not in FEATURES:
        FEATURES.append(_name)
for _name in ("heat_ge_30", "cold_le_minus5", "precipitation_ge_10"):
    FEATURES.extend(f"{_name}_sample_hours_prev_{_hours}h" for _hours in (6, 24, 72))
FEATURES.extend(["heat_excess_30_prev_24h_c_sample_hours", "cold_excess_minus5_prev_24h_c_sample_hours",
                 "precipitation_excess_10_prev_24h_mm"])
STATIC_FEATURES = FEATURES[20:26]


def utc(value):
    t = pd.Timestamp(value)
    if pd.isna(t) or t.tzinfo is None:
        raise ValueError("Use a non-null timestamp with an explicit timezone")
    return t.tz_convert("UTC")


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n", newline="\n")
    temp.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def require_columns(frame, columns, name):
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")

