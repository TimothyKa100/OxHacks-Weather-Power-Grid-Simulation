"""Monthly, route-weighted historical features. No incident outcomes enter this module."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import FEATURE_VERSION
from .common import FEATURES, STATIC_FEATURES, LOOKBACK_HOURS, now, sha256, utc, write_json
from .inventory import WEATHER_COLUMNS


def weighted_percentile(values, weights, quantile=.95):
    """Time x cell values; route length, not number of samples, defines the quantile."""
    order = np.argsort(values, axis=1)
    ranked = np.take_along_axis(values, order, axis=1)
    cumulative = np.cumsum(np.asarray(weights)[order], axis=1)
    index = (cumulative >= quantile * np.sum(weights)).argmax(axis=1)
    result = ranked[np.arange(len(values)), index]
    result[~np.isfinite(values).all(axis=1)] = np.nan
    return result


def crosswind_envelope(bearings):
    """Exact for the integer-degree direction values in these source Parquets."""
    bearings = np.asarray(bearings, dtype=float)
    if not len(bearings) or not np.isfinite(bearings).all():
        raise ValueError("Route bearings must be finite and nonempty")
    direction = np.arange(361, dtype=float)
    # Keep intermediate arrays bounded on long feeders.
    envelope = np.zeros(361)
    for start in range(0, len(bearings), 256):
        factor = np.abs(np.sin(np.deg2rad(direction[:, None] - bearings[None, start:start + 256])))
        envelope = np.maximum(envelope, factor.max(axis=1))
    return envelope


def rolling(series, hours, operation):
    return getattr(series.rolling(hours, min_periods=hours), operation)()


def derive_features(raw):
    """Instantaneous meteorology uses samples strictly before cutoff.

    Precipitation at cutoff t measures the completed interval [t-1h,t), so its
    rolling sums include the value timestamped t. This is historical replay;
    no operational availability/issue time is claimed for reanalysis.
    """
    if not raw.index.is_unique or not raw.index.is_monotonic_increasing:
        raise ValueError("Weather index must be unique and increasing")
    if not raw.index.to_series().diff().dropna().eq(pd.Timedelta(hours=1)).all():
        raise ValueError("Reindex weather to consecutive UTC hours; preserve missing values")
    out = pd.DataFrame(index=raw.index)
    gust = raw.gust_max.shift(1)
    out["gust_route_max_lag_1h_kmh"] = gust
    out["gust_route_p95_lag_1h_kmh"] = raw.gust_p95.shift(1)
    out["crosswind_route_max_lag_1h_kmh"] = raw.crosswind_max.shift(1)
    out["wind_speed_route_mean_lag_1h_kmh"] = raw.wind_mean.shift(1)
    for h in (3, 6, 24, 72):
        out[f"gust_max_prev_{h}h_kmh"] = rolling(gust, h, "max")
    out["precipitation_prev_1h_mm"] = raw.precipitation
    for h in (24, 72):
        out[f"precipitation_prev_{h}h_mm"] = rolling(raw.precipitation, h, "sum")
    temp = raw.temperature.shift(1)
    out["temperature_route_mean_lag_1h_c"] = temp
    out["temperature_min_prev_24h_c"] = rolling(temp, 24, "min")
    out["temperature_max_prev_24h_c"] = rolling(temp, 24, "max")
    out["soil_moisture_route_mean_lag_1h_m3_m3"] = raw.soil_moisture.shift(1)
    for threshold in (80, 100, 120):
        samples = gust.ge(threshold).astype(float).where(gust.notna())
        for hours in (6, 24, 72):
            out[f"gust_ge_{threshold}_sample_hours_prev_{hours}h"] = rolling(samples, hours, "sum")
        out[f"gust_excess_{threshold}_prev_24h_kmh_sample_hours"] = rolling((gust - threshold).clip(lower=0), 24, "sum")
    for name, values, threshold, op, intensity in [
            ("heat_ge_30", temp, 30, "ge", "heat_excess_30_prev_24h_c_sample_hours"),
            ("cold_le_minus5", temp, -5, "le", "cold_excess_minus5_prev_24h_c_sample_hours"),
            ("precipitation_ge_10", raw.precipitation, 10, "ge", "precipitation_excess_10_prev_24h_mm")]:
        samples = getattr(values, op)(threshold).astype(float).where(values.notna())
        for hours in (6, 24, 72):
            out[f"{name}_sample_hours_prev_{hours}h"] = rolling(samples, hours, "sum")
        excess = (values - threshold) if op == "ge" else (threshold - values)
        out[intensity] = rolling(excess.clip(lower=0), 24, "sum")
    out["month_sin"] = np.sin(2 * np.pi * (out.index.month - 1) / 12)
    out["month_cos"] = np.cos(2 * np.pi * (out.index.month - 1) / 12)
    out["hour_sin"] = np.sin(2 * np.pi * out.index.hour / 24)
    out["hour_cos"] = np.cos(2 * np.pi * out.index.hour / 24)
    return out


class FeatureBuilder:
    def __init__(self, output, weather_directory):
        self.output = Path(output)
        self.weather_directory = Path(weather_directory)
        self.assets = pd.read_parquet(self.output / "assets.parquet").sort_values("asset_id")
        self.links = pd.read_parquet(self.output / "route_weather_links.parquet")
        self.audit = pd.read_parquet(self.output / "weather_audit.parquet").set_index("weather_key")
        self.groups = {asset: group for asset, group in self.links.groupby("asset_id", sort=False)}
        self.envelopes = {(r.asset_id, r.weather_key): crosswind_envelope(r.bearing_degrees)
                          for r in self.links.itertuples()}

    def weather_for_period(self, start, end):
        index = pd.date_range(start, end, freq="h", inclusive="left")
        data = {}
        for key in sorted(self.links.weather_key.unique()):
            path = self.weather_directory / f"{key}.parquet"
            if not path.exists() or key not in self.audit.index or not self.audit.loc[key, "usable"]:
                data[key] = pd.DataFrame(np.nan, index=index, columns=WEATHER_COLUMNS)
                continue
            d = pd.read_parquet(path, columns=["time", *WEATHER_COLUMNS],
                                filters=[("time", ">=", start), ("time", "<", end)])
            d = d.set_index("time").reindex(index)
            if self.audit.loc[key, "soil_all_zero"]:
                d["soil_moisture_0_to_7cm"] = np.nan
            data[key] = d
        return index, data

    def asset_features(self, asset, index, weather):
        links = self.groups[asset.asset_id]
        weights = links.length_fraction.to_numpy()
        matrices = {c: np.column_stack([weather[key][c].to_numpy() for key in links.weather_key])
                    for c in WEATHER_COLUMNS}
        gust = matrices["wind_gusts_10m"]
        direction = matrices["wind_direction_10m"]
        cross = []
        for i, key in enumerate(links.weather_key):
            theta = direction[:, i]
            valid = np.isfinite(theta)
            if not np.equal(theta[valid], np.round(theta[valid])).all():
                raise ValueError("Noninteger wind directions need an exact crosswind calculation")
            factor = np.full(len(index), np.nan)
            factor[valid] = self.envelopes[(asset.asset_id, key)][np.mod(theta[valid].astype(int), 360)]
            cross.append(gust[:, i] * factor)
        raw = pd.DataFrame({"gust_max": np.max(gust, axis=1),
                            "gust_p95": weighted_percentile(gust, weights),
                            "crosswind_max": np.max(np.column_stack(cross), axis=1),
                            "wind_mean": matrices["wind_speed_10m"] @ weights,
                            "precipitation": matrices["precipitation"] @ weights,
                            "temperature": matrices["temperature_2m"] @ weights,
                            "soil_moisture": matrices["soil_moisture_0_to_7cm"] @ weights}, index=index)
        if not asset.route_weather_complete:
            raw.loc[:, :] = np.nan
        out = derive_features(raw)
        for col in STATIC_FEATURES:
            out[col] = getattr(asset, col)
        return out

    def batch_features(self, index, weather, start):
        """Aggregate routes separately, then roll all feeders in one vectorized batch."""
        count = len(self.assets)
        shape = (len(index), count)
        raw = {name: np.full(shape, np.nan) for name in
               ["gust_max", "gust_p95", "crosswind_max", "wind_mean", "precipitation", "temperature", "soil_moisture"]}
        for col, asset in enumerate(self.assets.itertuples()):
            if not asset.route_weather_complete:
                continue
            links = self.groups[asset.asset_id]
            weights = links.length_fraction.to_numpy()
            matrices = {c: np.column_stack([weather[key][c].to_numpy() for key in links.weather_key])
                        for c in WEATHER_COLUMNS}
            gust = matrices["wind_gusts_10m"]
            raw["gust_max"][:, col] = gust.max(axis=1)
            raw["gust_p95"][:, col] = weighted_percentile(gust, weights)
            cross = []
            for k, key in enumerate(links.weather_key):
                theta = matrices["wind_direction_10m"][:, k]
                valid = np.isfinite(theta)
                if not np.equal(theta[valid], np.round(theta[valid])).all():
                    raise ValueError("Noninteger directions need exact crosswind calculation")
                factor = np.full(len(index), np.nan)
                factor[valid] = self.envelopes[(asset.asset_id, key)][np.mod(theta[valid].astype(int), 360)]
                cross.append(gust[:, k] * factor)
            raw["crosswind_max"][:, col] = np.max(np.column_stack(cross), axis=1)
            for name, source in [("wind_mean", "wind_speed_10m"), ("precipitation", "precipitation"),
                                 ("temperature", "temperature_2m"), ("soil_moisture", "soil_moisture_0_to_7cm")]:
                raw[name][:, col] = (matrices[source] * weights[None, :]).sum(axis=1)
        def shift(values):
            return np.vstack([np.full((1, count), np.nan), values[:-1]])
        def roll(values, hours, operation):
            return getattr(pd.DataFrame(values).rolling(hours, min_periods=hours), operation)().to_numpy()
        gust, temp = shift(raw["gust_max"]), shift(raw["temperature"])
        values = {"gust_route_max_lag_1h_kmh": gust,
                  "gust_route_p95_lag_1h_kmh": shift(raw["gust_p95"]),
                  "crosswind_route_max_lag_1h_kmh": shift(raw["crosswind_max"]),
                  "wind_speed_route_mean_lag_1h_kmh": shift(raw["wind_mean"]),
                  "precipitation_prev_1h_mm": raw["precipitation"],
                  "temperature_route_mean_lag_1h_c": temp,
                  "soil_moisture_route_mean_lag_1h_m3_m3": shift(raw["soil_moisture"])}
        for hours in (3, 6, 24, 72):
            values[f"gust_max_prev_{hours}h_kmh"] = roll(gust, hours, "max")
        for hours in (24, 72):
            values[f"precipitation_prev_{hours}h_mm"] = roll(raw["precipitation"], hours, "sum")
        for operation in ("min", "max"):
            values[f"temperature_{operation}_prev_24h_c"] = roll(temp, 24, operation)
        for threshold in (80, 100, 120):
            above = np.where(np.isfinite(gust), (gust >= threshold).astype(float), np.nan)
            for hours in (6, 24, 72):
                values[f"gust_ge_{threshold}_sample_hours_prev_{hours}h"] = roll(above, hours, "sum")
            values[f"gust_excess_{threshold}_prev_24h_kmh_sample_hours"] = roll(np.maximum(gust - threshold, 0), 24, "sum")
        for name, data, threshold, op, intensity in [
                ("heat_ge_30", temp, 30, "ge", "heat_excess_30_prev_24h_c_sample_hours"),
                ("cold_le_minus5", temp, -5, "le", "cold_excess_minus5_prev_24h_c_sample_hours"),
                ("precipitation_ge_10", raw["precipitation"], 10, "ge", "precipitation_excess_10_prev_24h_mm")]:
            above = data >= threshold if op == "ge" else data <= threshold
            above = np.where(np.isfinite(data), above.astype(float), np.nan)
            for hours in (6, 24, 72):
                values[f"{name}_sample_hours_prev_{hours}h"] = roll(above, hours, "sum")
            excess = data - threshold if op == "ge" else threshold - data
            values[intensity] = roll(np.maximum(excess, 0), 24, "sum")
        keep = index >= start
        times = index[keep]
        n = len(times)
        columns = {"asset_id": pa.array(np.repeat(self.assets.asset_id.to_numpy(), n)),
                   "cutoff_utc": pa.array(np.tile(times.asi8, count), type=pa.timestamp("ns", tz="UTC"))}
        for feature in FEATURES:
            if feature in values:
                array = values[feature][keep].ravel(order="F")
            elif feature in STATIC_FEATURES:
                array = np.repeat(self.assets[feature].to_numpy(), n)
            else:
                angle = 2 * np.pi * ((times.month - 1) / 12 if feature.startswith("month") else times.hour / 24)
                array = np.tile(np.sin(angle) if feature.endswith("sin") else np.cos(angle), count)
            columns[feature] = pa.array(array, type=pa.float32(), from_pandas=True)
        columns["horizon_hours"] = pa.array(np.ones(n * count, dtype=np.int8))
        return pa.table(columns)

    def build(self, start, end, resume=True):
        start, end = utc(start), utc(end)
        if start != start.floor("h") or end != end.floor("h") or end <= start:
            raise ValueError("Feature interval must be increasing, aligned UTC hours; end is exclusive")
        inventory = json.loads((self.output / "inventory_report.json").read_text())
        config = {"start_utc": start.isoformat(), "end_utc_exclusive": end.isoformat(),
                  "feature_version": FEATURE_VERSION, "features": FEATURES, "asset_sha256": sha256(self.output / "assets.parquet"),
                  "route_links_sha256": sha256(self.output / "route_weather_links.parquet"),
                  "weather_checksums": self.audit.loc[self.audit.index.isin(self.links.weather_key), "parquet_sha256"].to_dict(),
                  "operator": inventory["operator"]}
        manifest_path = self.output / "feature_manifest.json"
        previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        compatible = previous.get("configuration") == config
        if previous and not compatible and resume:
            raise ValueError("Existing feature run has a different configuration; use a new output or --rebuild")
        if not resume:
            for folder in ["features", "labels"]:
                for path in (self.output / folder).glob("*.parquet"):
                    path.unlink()
        parts = previous.get("partitions", {}) if compatible and resume else {}
        boundary = start
        while boundary < end:
            following = min(boundary.normalize() + pd.offsets.MonthBegin(1), end)
            period = boundary.strftime("%Y-%m")
            path = self.output / "features" / f"{period}.parquet"
            label_path = self.output / "labels" / f"{period}.parquet"
            if period in parts and path.exists() and label_path.exists():
                boundary = following
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            label_path.parent.mkdir(parents=True, exist_ok=True)
            index, weather = self.weather_for_period(boundary - pd.Timedelta(hours=LOOKBACK_HOURS), following)
            features = self.batch_features(index, weather, boundary)
            rows = features.num_rows
            labels = pa.table({"asset_id": features["asset_id"], "cutoff_utc": features["cutoff_utc"],
                               "target": pa.nulls(rows, type=pa.int8()),
                               "at_risk_verified": np.zeros(rows, dtype=bool), "label_verified": np.zeros(rows, dtype=bool),
                               "censor_reason": pa.array(["unverified_events_availability_and_identity"] * rows)})
            pq.write_table(features, path.with_suffix(".tmp"), compression="zstd", row_group_size=100000)
            path.with_suffix(".tmp").replace(path)
            pq.write_table(labels, label_path.with_suffix(".tmp"), compression="zstd", row_group_size=100000)
            label_path.with_suffix(".tmp").replace(label_path)
            parts[period] = {"rows": features.num_rows, "feature_bytes": path.stat().st_size,
                             "start_utc": boundary.isoformat(), "end_utc_exclusive": following.isoformat()}
            report = {"generated_utc": now(), "configuration": config, "partitions": parts,
                      "rows": sum(p["rows"] for p in parts.values()), "complete": following == end,
                      "training_ready": False, "mode": "historical_reanalysis_replay",
                      "gust_convention": "raw 10m weather-grid gust; no engineering terrain multiplier",
                      "precipitation_time": "value at t represents [t-1h,t); sums include t",
                      "instantaneous_time": "samples strictly before cutoff; no operational issue-time claim"}
            write_json(manifest_path, report)
            print(f"Features {period}: {features.num_rows:,} rows; {report['rows']:,} total", flush=True)
            boundary = following
        return json.loads(manifest_path.read_text())
