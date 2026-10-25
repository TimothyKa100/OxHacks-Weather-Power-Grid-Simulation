"""Validate materialized partitions against source routes and eligibility contracts."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .common import FEATURES, now, write_json
from .features import FeatureBuilder
from .training import training_readiness


def validate_dataset(output, weather_directory):
    output = Path(output)
    manifest = json.loads((output / "feature_manifest.json").read_text())
    if not manifest["complete"]:
        raise ValueError("Feature run has not completed")
    assets = pd.read_parquet(output / "assets.parquet")
    links = pd.read_parquet(output / "route_weather_links.parquet")
    audit = pd.read_parquet(output / "weather_audit.parquet")
    if not assets.asset_id.is_unique or not assets.gis_circuit_key.is_unique:
        raise ValueError("Asset identities are not one-to-one")
    if not np.allclose(links.groupby("asset_id").length_fraction.sum(), 1):
        raise ValueError("Route length fractions must sum to one")
    if not assets.route_length_coverage.between(.999, 1.001).all():
        raise ValueError("Missing route intervals")
    if not audit.usable.all() or not audit.raw_checksum_verified.all():
        raise ValueError("Weather continuity, values or raw checksum validation failed")
    total, positives, negatives, observed_nulls = 0, 0, 0, 0
    for period, part in sorted(manifest["partitions"].items()):
        path = output / "features" / f"{period}.parquet"
        schema = pq.read_schema(path)
        if not set(FEATURES).issubset(schema.names) or {"cause", "damage", "target", "event_id", "avg_time_off_mins"} & set(schema.names):
            raise ValueError("Feature schema contains outcomes or lacks predictors")
        keys = pd.read_parquet(path, columns=["asset_id", "cutoff_utc", "gust_max_prev_72h_kmh", "horizon_hours"])
        if len(keys) != part["rows"] or keys.duplicated(["asset_id", "cutoff_utc"]).any() or not keys.horizon_hours.eq(1).all():
            raise ValueError(f"Invalid asset-hour keys: {period}")
        hours = pd.date_range(part["start_utc"], part["end_utc_exclusive"], inclusive="left", freq="h")
        expected = len(assets) * len(hours)
        if len(keys) != expected or not keys.asset_id.value_counts().eq(len(hours)).all():
            raise ValueError(f"Incomplete monthly cohort: {period}")
        if not keys.cutoff_utc.isin(hours).all():
            raise ValueError(f"Unexpected timestamps: {period}")
        labels = pd.read_parquet(output / "labels" / path.name)
        if not keys[["asset_id", "cutoff_utc"]].equals(labels[["asset_id", "cutoff_utc"]]):
            raise ValueError(f"Feature/label keys differ: {period}")
        bad = labels.target.notna() & (~labels.label_verified.eq(True) | ~labels.at_risk_verified.eq(True))
        if bad.any():
            raise ValueError("An unverified/censored row has a supervised target")
        positives += int(labels.target.eq(1).sum())
        negatives += int(labels.target.eq(0).sum())
        observed_nulls += int(keys.gust_max_prev_72h_kmh.isna().sum())
        total += len(keys)
    if total != manifest["rows"]:
        raise ValueError("Feature manifest row total mismatch")
    config = manifest["configuration"]
    first, last = pd.Timestamp(config["start_utc"]), pd.Timestamp(config["end_utc_exclusive"])
    total_hours = int((last - first) / pd.Timedelta(hours=1))
    warmup_end = audit.start_utc.max() + pd.Timedelta(hours=72)
    warmup_hours = max(0, min(total_hours, int((warmup_end - first) / pd.Timedelta(hours=1))))
    covered = int(assets.route_weather_complete.sum())
    expected_nulls = (len(assets) - covered) * total_hours + covered * warmup_hours
    if observed_nulls != expected_nulls:
        raise ValueError(f"Unexpected 72h gust missingness: {observed_nulls}, expected {expected_nulls}")
    # Independent single-feeder computation checks batch math and monthly boundaries.
    builder = FeatureBuilder(output, weather_directory)
    samples = []
    periods = sorted(manifest["partitions"])
    for period in sorted(set([periods[0], periods[len(periods) // 2], periods[-1]])):
        part = manifest["partitions"][period]
        start, end = pd.Timestamp(part["start_utc"]), pd.Timestamp(part["end_utc_exclusive"])
        index, weather = builder.weather_for_period(start - pd.Timedelta(hours=72), end)
        for position in [0, len(builder.assets) // 2, len(builder.assets) - 1]:
            asset = next(builder.assets.iloc[[position]].itertuples())
            independent = builder.asset_features(asset, index, weather).loc[start:end - pd.Timedelta(hours=1), FEATURES]
            stored = pd.read_parquet(output / "features" / f"{period}.parquet", filters=[("asset_id", "==", asset.asset_id)])
            if not np.allclose(stored[FEATURES].to_numpy(float), independent.to_numpy(float), rtol=2e-6, atol=2e-5, equal_nan=True):
                raise ValueError(f"Batch/source feature discrepancy: {asset.asset_id}, {period}")
            samples.append({"asset_id": asset.asset_id, "period": period, "rows": len(stored)})
    result = {"generated_utc": now(), "integrity_checks_passed": True, "asset_hour_rows": total,
              "monthly_partitions": len(manifest["partitions"]), "feeders": len(assets),
              "route_weather_complete_feeders": covered,
              "verified_positive_rows": positives, "verified_negative_rows": negatives,
              "null_72h_gust_rows": observed_nulls, "source_recomputations": samples,
              "training_readiness": training_readiness(output),
              "scope": "Data integrity and feature reproducibility; no failure calibration or power-flow validity claim"}
    write_json(output / "validation_report.json", result)
    return result
