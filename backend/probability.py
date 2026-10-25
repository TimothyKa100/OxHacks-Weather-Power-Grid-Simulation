"""Strict hourly branch-probability import contract for an explicitly named network."""
from datetime import datetime, timedelta, timezone
import math

import numpy as np


def timestamp(value, field):
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if t.utcoffset() is None:
            raise ValueError()
        return t.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"{field} must be an ISO timestamp with a timezone") from None


def number(value, field, low=0., high=float("inf")):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{field} must be finite and between {low} and {high}")
    return float(value)


def validate_profile(profile, network):
    if not isinstance(profile, dict):
        raise ValueError("weather must be an object")
    if profile.get("network") != "ieee14" or profile.get("prediction_unit") != "branch":
        raise ValueError("This simulator accepts ieee14 branch probabilities; feeder/GB probabilities need a verified network adapter")
    expected = [branch["id"] for branch in network.branches]
    if profile.get("branch_ids") != expected:
        raise ValueError("branch_ids must exactly match /api/network?name=ieee14 order")
    if not isinstance(profile.get("source"), str) or not profile["source"].strip():
        raise ValueError("A probability source is required")
    prediction = profile.get("prediction")
    if not isinstance(prediction, dict) or not isinstance(prediction.get("model_version"), str) or not prediction["model_version"].strip():
        raise ValueError("prediction.model_version is required")
    training_cutoff = timestamp(prediction.get("training_cutoff"), "training_cutoff")
    issued = timestamp(prediction.get("issued_at"), "issued_at")
    if training_cutoff > issued:
        raise ValueError("training_cutoff must not follow issued_at")
    mode = profile.get("mode")
    if mode not in ("forecast", "historical_replay", "synthetic_test"):
        raise ValueError("mode must be forecast, historical_replay or synthetic_test")
    hours = profile.get("hours")
    if not isinstance(hours, list) or not 1 <= len(hours) <= 72:
        raise ValueError("Provide 1–72 consecutive hourly records")
    result = []
    previous = None
    for hour in hours:
        if not isinstance(hour, dict):
            raise ValueError("Each hour must be an object")
        t = timestamp(hour.get("timestamp"), "hour timestamp")
        if t.minute or t.second or t.microsecond or (previous is not None and t - previous != timedelta(hours=1)):
            raise ValueError("Timestamps must be aligned consecutive UTC hours")
        if training_cutoff > t or (mode == "forecast" and issued > t):
            raise ValueError("Model training/forecast issue time must not follow the prediction cutoff")
        previous = t
        values = {}
        for field, upper in [("failure_probability", 1.), ("gust_kmh", float("inf"))]:
            vector = hour.get(field)
            if not isinstance(vector, list) or len(vector) != network.m:
                raise ValueError(f"{field} needs one value per branch")
            values[field] = [number(v, field, high=upper) for v in vector]
        result.append({"timestamp": t.isoformat(), **values,
                       "rain_mm_h": number(hour.get("rain_mm_h"), "rain_mm_h")})
    return {"network": "ieee14", "prediction_unit": "branch", "source": profile["source"], "mode": mode,
            "branch_ids": expected, "prediction": {**prediction, "training_cutoff": training_cutoff.isoformat(),
                                                    "issued_at": issued.isoformat()}, "hours": result}


def imported_scenarios(profile, seed, count):
    probabilities = np.asarray([h["failure_probability"] for h in profile["hours"]], dtype=float)
    gust = np.asarray([h["gust_kmh"] for h in profile["hours"]], dtype=float)
    rain = np.asarray([h["rain_mm_h"] for h in profile["hours"]], dtype=float)
    draws = np.random.default_rng(seed).random((count, *probabilities.shape))
    return [{"gust": gust, "rain": rain, "failure_probability": probabilities, "draws": draw,
             "timestamps": [h["timestamp"] for h in profile["hours"]]} for draw in draws]


def reduce_hazard(probability, factor=.25):
    """Assumed intervention effect; it is not estimated by the failure model."""
    p = np.asarray(probability, dtype=float)
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any() or not 0 <= factor <= 1:
        raise ValueError("Invalid probability or hazard multiplier")
    if factor == 0:
        return np.zeros_like(p)
    return -np.expm1(np.log1p(-np.minimum(p, 1 - np.finfo(float).eps)) * factor) * (p < 1) + (p == 1)
