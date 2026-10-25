"""Inspect actual weather files and assemble source-preserving route associations."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Geod, Transformer

from .common import ROOT, now, sha256, write_json

WEATHER_COLUMNS = ["wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
                   "temperature_2m", "precipitation", "soil_moisture_0_to_7cm"]


def cell_key(latitude, longitude, spacing=0.5):
    lat = round(float(latitude) / spacing) * spacing
    lon = round(float(longitude) / spacing) * spacing
    # Signed zero is not an identity difference.
    return f"{lat + 0.0:.2f}_{lon + 0.0:.2f}"


def audit_weather(processed, manifest, output):
    """Read every timestamp and required variable; don't trust old availability flags."""
    metadata = {r["weather_key"]: r for r in manifest if r.get("status") == "downloaded"}
    rows = []
    for path in sorted((Path(processed) / "weather").glob("*.parquet")):
        d = pd.read_parquet(path, columns=["time", *WEATHER_COLUMNS, "snow_depth"])
        t = d.time
        if not isinstance(t.dtype, pd.DatetimeTZDtype) or str(t.dt.tz) != "UTC":
            raise ValueError(f"{path}: weather time must be timezone-aware UTC")
        meta = metadata.get(path.stem, {})
        row = dict(weather_key=path.stem, rows=len(d), start_utc=t.min(), end_utc=t.max(),
                   duplicate_hours=int(t.duplicated().sum()),
                   non_hour_steps=int(t.diff().dropna().ne(pd.Timedelta(hours=1)).sum()),
                   off_hour_timestamps=int(t.ne(t.dt.floor("h")).sum()),
                   provider_latitude=meta.get("latitude"), provider_longitude=meta.get("longitude"),
                   provider_elevation_m=meta.get("elevation_m"),
                   soil_all_zero=bool(d.soil_moisture_0_to_7cm.eq(0).all()),
                   snow_depth_nulls=int(d.snow_depth.isna().sum()),
                   parquet_sha256=sha256(path), raw_checksum_verified=False)
        for c in WEATHER_COLUMNS:
            row[c + "_nulls"] = int(d[c].isna().sum())
            row[c + "_min"] = float(d[c].min()) if d[c].notna().any() else None
            row[c + "_max"] = float(d[c].max()) if d[c].notna().any() else None
        raw = ROOT / meta.get("path", "__missing__")
        if raw.is_file() and meta.get("sha256"):
            row["raw_checksum_verified"] = sha256(raw) == meta["sha256"]
            if not row["raw_checksum_verified"]:
                raise ValueError(f"Weather raw checksum mismatch: {raw}")
        row["usable"] = (len(d) > 0 and not row["duplicate_hours"] and
                         not row["non_hour_steps"] and not row["off_hour_timestamps"] and
                         all(row[c + "_nulls"] == 0 for c in WEATHER_COLUMNS[:5]))
        rows.append(row)
    if not rows:
        raise ValueError("No weather Parquet files found")
    audit = pd.DataFrame(rows)
    audit.to_parquet(Path(output) / "weather_audit.parquet", index=False)
    return audit


def route_intervals(profiles):
    """Each consecutive route sample pair contributes its actual along-route length."""
    p = profiles.sort_values(["node_id", "part", "distance_m"]).copy()
    previous = p.groupby(["node_id", "part"], sort=False).shift(1)
    length = p.distance_m - previous.distance_m
    keep = length.gt(0)
    p = p.loc[keep].copy()
    previous = previous.loc[keep]
    p["route_length_m"] = length[keep]
    conv = Transformer.from_crs(27700, 4326, always_xy=True)
    lon0, lat0 = conv.transform(previous.easting.to_numpy(), previous.northing.to_numpy())
    lon1, lat1 = conv.transform(p.easting.to_numpy(), p.northing.to_numpy())
    lon, lat = conv.transform((previous.easting.to_numpy() + p.easting.to_numpy()) / 2,
                              (previous.northing.to_numpy() + p.northing.to_numpy()) / 2)
    bearings = Geod(ellps="WGS84").inv(lon0, lat0, lon1, lat1)[0]
    p["bearing_deg"] = np.mod(bearings, 180)
    p["weather_key"] = [cell_key(a, b) for a, b in zip(lat, lon)]
    a, b = previous.terrain_elevation_m.to_numpy(), p.terrain_elevation_m.to_numpy()
    valid = np.isfinite(a) & np.isfinite(b)
    p["terrain_integral_m2"] = np.where(valid, (a + b) / 2 * p.route_length_m, 0)
    p["terrain_valid_length_m"] = np.where(valid, p.route_length_m, 0)
    p["terrain_interval_min_m"] = np.where(valid, np.minimum(a, b), np.nan)
    p["terrain_interval_max_m"] = np.where(valid, np.maximum(a, b), np.nan)
    return p


def build_inventory(output, operator="SEPD", processed=None, normalized=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    processed = Path(processed or ROOT / "data/processed")
    normalized = Path(normalized or ROOT / "data/grid/normalized")
    manifest_path = ROOT / "data/weather_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    audit = audit_weather(processed, manifest, output)
    roster = pd.read_parquet(processed / "circuit_roster.parquet")
    old_cross = pd.read_parquet(processed / "roster_weather_crosswalk.parquet")
    available = set(audit.loc[audit.usable, "weather_key"])
    refreshed = old_cross.copy()
    refreshed["weather_key"] = [cell_key(a, b) for a, b in
                                 zip(refreshed.sample_latitude, refreshed.sample_longitude)]
    refreshed["previous_weather_available"] = old_cross.weather_available
    refreshed["weather_available"] = refreshed.weather_key.isin(available)
    refreshed["weather_grid_elevation_m"] = refreshed.weather_key.map(audit.set_index("weather_key").provider_elevation_m)
    refreshed.to_parquet(output / "roster_weather_crosswalk.parquet", index=False)

    selected = roster[roster.gis_circuit_key_candidate.notna()].copy()
    if operator != "ALL":
        selected = selected[selected.operator.eq(operator)].copy()
    if selected.empty:
        raise ValueError(f"No GIS-matched feeders for {operator}")
    if selected.gis_circuit_key_candidate.duplicated().any():
        raise ValueError("Multiple feeders map to one GIS circuit; resolve before assembly")
    selected = selected.rename(columns={"circuit_key": "asset_id", "gis_circuit_key_candidate": "gis_circuit_key"})
    seg = pd.read_parquet(processed / "overhead_segments.parquet", columns=["segment_id", "circuit_key", "length_km"])
    seg["node_id"] = "SSEN:line:" + seg.segment_id
    seg = seg.merge(selected[["asset_id", "gis_circuit_key"]], left_on="circuit_key", right_on="gis_circuit_key", validate="many_to_one")
    profiles = pd.read_parquet(normalized / "SSEN_routes_elevation_profiles.parquet")
    profiles = profiles[profiles.node_id.isin(seg.node_id)]
    pieces = route_intervals(profiles).merge(seg[["node_id", "segment_id", "asset_id"]], on="node_id", validate="many_to_one")
    # Geographic source routes are rounded separately from the authoritative BNG
    # lengths. Reconcile interval weights to each published BNG segment length.
    sampled = pieces.groupby("node_id").route_length_m.sum()
    lengths = seg.set_index("node_id").length_km * 1000
    scale = pieces.node_id.map(lengths / sampled)
    for col in ["route_length_m", "terrain_valid_length_m", "terrain_integral_m2"]:
        pieces[col] *= scale
    pieces["weather_available"] = pieces.weather_key.isin(available)
    pieces[["asset_id", "segment_id", "node_id", "part", "route_length_m", "weather_key", "bearing_deg",
            "weather_available", "terrain_valid_length_m"]].to_parquet(output / "route_weather_pieces.parquet", index=False)
    links = pieces.groupby(["asset_id", "weather_key"], sort=True).agg(
        route_length_m=("route_length_m", "sum"),
        bearing_degrees=("bearing_deg", lambda x: sorted(set(x))),
        weather_available=("weather_available", "all")).reset_index()
    links["length_fraction"] = links.route_length_m / links.groupby("asset_id").route_length_m.transform("sum")
    links["association"] = "route_interval_midpoint_to_0.5_degree_request_lattice"
    links.to_parquet(output / "route_weather_links.parquet", index=False)

    terrain = pieces.groupby("asset_id").agg(
        terrain_min_m=("terrain_interval_min_m", "min"), terrain_max_m=("terrain_interval_max_m", "max"),
        terrain_integral_m2=("terrain_integral_m2", "sum"), terrain_valid_length_m=("terrain_valid_length_m", "sum"),
        sampled_route_length_m=("route_length_m", "sum"), route_weather_complete=("weather_available", "all"))
    terrain["terrain_mean_m"] = terrain.terrain_integral_m2 / terrain.terrain_valid_length_m.replace(0, np.nan)
    terrain["terrain_length_coverage"] = terrain.terrain_valid_length_m / terrain.sampled_route_length_m
    assets = selected[["asset_id", "operator", "gis_circuit_key", "primary_nrn", "hv_feeder_nrn", "primary_name",
                       "longitude", "latitude", "overhead_length_km", "voltage_kv", "woodland_exposure_fraction"]].merge(
                           terrain.drop(columns="terrain_integral_m2"), on="asset_id", how="left", validate="one_to_one")
    # Incomplete height profiles cannot masquerade as whole-route terrain summaries.
    assets.loc[assets.terrain_length_coverage.lt(1 - 1e-6), ["terrain_min_m", "terrain_mean_m", "terrain_max_m"]] = np.nan
    assets["route_length_coverage"] = assets.sampled_route_length_m / (assets.overhead_length_km * 1000)
    assets["route_weather_complete"] = assets.route_weather_complete.fillna(False) & assets.route_length_coverage.between(.999, 1.001)
    assets["identity_verified"] = False
    assets["historical_asset_validity_verified"] = False
    assets["asset_snapshot"] = "2026-10"
    assets["woodland_snapshot"] = "2024"
    assets["terrain_source"] = "OS Terrain 50; route interval integration"
    assets["prediction_unit"] = "feeder"
    assets.to_parquet(output / "assets.parquet", index=False)
    missing = links[~links.weather_available].groupby("weather_key").agg(affected_feeders=("asset_id", "nunique")).reset_index()
    missing["status"] = "not_downloaded"
    missing.to_parquet(output / "weather_requests_pending.parquet", index=False)
    sources = [processed / p for p in ["circuit_roster.parquet", "overhead_segments.parquet", "fault_candidates_UNVERIFIED.parquet"]]
    sources += [normalized / "SSEN_routes_elevation_profiles.parquet", manifest_path]
    result = dict(generated_utc=now(), operator=operator, feeders=len(assets),
                  route_weather_complete_feeders=int(assets.route_weather_complete.sum()),
                  route_weather_links=len(links), route_intervals=len(pieces),
                  weather_files=len(audit), weather_rows=int(audit.rows.sum()),
                  weather_start_utc=str(audit.start_utc.min()), weather_end_utc=str(audit.end_utc.max()),
                  stale_roster_flags=int(refreshed.weather_available.ne(old_cross.weather_available).sum()),
                  roster_with_weather=int(refreshed.weather_available.sum()),
                  soil_all_zero_files=int(audit.soil_all_zero.sum()),
                  sources={str(p.relative_to(ROOT)): sha256(p) for p in sources},
                  supervised_training_ready=False,
                  limitations=["Feeder/GIS identities unverified", "Current asset and 2024 woodland snapshots are historical proxies",
                               "Historical reanalysis has no operational issue times", "Incident status, restoration and reporting completeness unresolved"])
    write_json(output / "inventory_report.json", result)
    return result
