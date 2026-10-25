"""Fixed chronological partitions, storm boundary purges and primary-area holdout."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .common import LOOKBACK_HOURS, HORIZON_HOURS, now, write_json

BOUNDARIES = [pd.Timestamp(f"{year}-01-01", tz="UTC") for year in (2022, 2023, 2024, 2026)]
NAMES = ["train", "tune", "calibrate", "test", "monitor"]


def build_storm_windows(output, weather_directory):
    """Exploratory weather clusters, not an official named-storm catalogue."""
    output = Path(output)
    links = pd.read_parquet(output / "route_weather_links.parquet")
    maxima = []
    for key in sorted(links.loc[links.weather_available, "weather_key"].unique()):
        d = pd.read_parquet(Path(weather_directory) / f"{key}.parquet", columns=["time", "wind_gusts_10m"])
        maxima.append(d.set_index("time").wind_gusts_10m.rename(key))
    peak = pd.concat(maxima, axis=1).max(axis=1).sort_index()
    active = peak[peak.ge(80)]
    rows = []
    if not active.empty:
        clusters = active.index.to_series().diff().gt(pd.Timedelta(hours=72)).cumsum()
        for number, (_, group) in enumerate(active.groupby(clusters.to_numpy()), 1):
            rows.append({"storm_id": f"weather-cluster-{number:04d}",
                         "start_utc": group.index.min() - pd.Timedelta(hours=24),
                         "end_utc": group.index.max() + pd.Timedelta(hours=25),
                         "peak_gust_kmh": group.max(), "active_sample_hours": len(group),
                         "definition": "any cohort request >=80 km/h; merge active gaps <=72h; 24h shoulders"})
    windows = pd.DataFrame(rows, columns=["storm_id", "start_utc", "end_utc", "peak_gust_kmh", "active_sample_hours", "definition"])
    for c in ["start_utc", "end_utc"]:
        windows[c] = pd.to_datetime(windows[c], utc=True)
    windows.to_parquet(output / "storm_windows.parquet", index=False)
    if (output / "feature_manifest.json").exists():
        config = json.loads((output / "feature_manifest.json").read_text())["configuration"]
        assets = pd.read_parquet(output / "assets.parquet")
        clock = pd.date_range(config["start_utc"], config["end_utc_exclusive"], freq="h", inclusive="left")
        representative = pd.DataFrame({"asset_id": assets.asset_id.iloc[0], "cutoff_utc": clock})
        primary = (assets.set_index("asset_id").operator + ":" + assets.set_index("asset_id").primary_nrn).to_dict()
        split = assign_splits(representative, windows, primary)
        durations = (windows.end_utc - windows.start_utc).dt.total_seconds() / 86400
        held = [asset for asset, key in primary.items() if int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 5 == 0]
        write_json(output / "split_audit.json", {
            "generated_utc": now(), "weather_clusters": len(windows),
            "cutoff_hours_by_split": split.split.value_counts().to_dict(),
            "candidate_asset_hours_by_split": (split.split.value_counts() * len(assets)).to_dict(),
            "spatial_holdout_feeders": len(held), "fitting_cohort_feeders": len(assets) - len(held),
            "clusters_over_30_days": int(durations.gt(30).sum()),
            "longest_weather_cluster_days": float(durations.max()) if len(durations) else None,
            "purged_fraction": float(split.split.eq("purged").mean()),
            "limitations": ["Threshold clusters may merge multiple named storms; conservative grouping is not an official storm catalogue",
                            "Inspect cluster spans and purged exposure before training; current target labels remain unverified"]})
    return windows


def assign_splits(frame, windows, asset_primary):
    out = frame[["asset_id", "cutoff_utc"]].copy()
    t = out.cutoff_utc
    out["split"] = np.asarray(NAMES)[np.searchsorted(np.array([b.value for b in BOUNDARIES]), t.astype("int64"), side="right")]
    out["storm_id"] = "quiet-" + t.dt.strftime("%Y-%m")
    purge = pd.Timedelta(hours=LOOKBACK_HOURS + HORIZON_HOURS)
    for boundary in BOUNDARIES:
        out.loc[(t - boundary).abs().le(purge), "split"] = "purged"
    for storm in windows.itertuples():
        mask = t.ge(storm.start_utc) & t.lt(storm.end_utc)
        out.loc[mask, "storm_id"] = storm.storm_id
        if any(storm.start_utc < b + purge and storm.end_utc > b - purge for b in BOUNDARIES):
            out.loc[mask, "split"] = "purged"
    # Group feeders sharing a primary rather than randomly splitting adjacent feeders.
    def held_out(primary):
        return int(hashlib.sha256(str(primary).encode()).hexdigest()[:8], 16) % 5 == 0
    out["spatial_holdout"] = out.asset_id.map(asset_primary).map(held_out)
    return out
