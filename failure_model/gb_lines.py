"""GB transmission-line corridors: SSEN-labelled outage risk and hourly route features.

Prediction unit: one simulator branch (data/gb_network.json, L001...) x one UTC hour.
Target (area level, not a verified line failure): at least one new weather-caused
overhead-line fault reported by SSEN Distribution (SEPD/SHEPD NaFIRS register) with
a candidate location inside the branch's 5 km corridor during [t, t+1h).

Distribution reports cannot identify a failed transmission asset, so this is an
outage-risk proxy for the area each line crosses. Labels exist only where the SSEN
register gives continuous reporting coverage (corridors at least 90% inside SSEN
primary supply areas); elsewhere an absent report is not a confirmed negative.
"""
import glob
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .common import ROOT, now, write_json
from .features import crosswind_envelope, weighted_percentile
from .inventory import cell_key

RADIUS_M = 5000
TERRITORY_MIN = 0.9
DEDUP_HOURS = 6
WOODLAND_BUFFER_M = 30
SEGMENT_DEG = 0.02  # ~2 km route segments for cell lengths and bearings
# Register coverage: first report 2015-04-01; export retrieved after 18 Oct 2026.
COVERAGE = (pd.Timestamp("2015-04-01", tz="UTC"), pd.Timestamp("2026-10-18", tz="UTC"))
WEATHER_CAUSES = ("Wind and Gale", "Windborne Materials", "Falling live trees", "Falling dead trees",
                  "Lightning", "Ice", "Snow, Sleet", "Rain", "Freezing Fog", "Flooding", "Solar Heat")
WEATHER_FEATURES = ["gust_max_kmh", "gust_p95_kmh", "gust_prev_3h_max_kmh", "crosswind_max_kmh",
                    "rain_1h_mm", "rain_prev_24h_mm", "temperature_c"]
ASSET_FEATURES = ["length_km", "ele_mean_m", "ele_max_m", "kv", "circuits"]
FEATURES = WEATHER_FEATURES + ASSET_FEATURES
VEGETATION = ["vegetation_fraction"]
# Estimated from route length and voltage class: engineering assumptions, not measurements.
DERIVED = ["pylons", "span_m", "tower_height_m"]
OUTPUT = ROOT / "data/failure_model/gb_corridor"


def load_network(path=ROOT / "data/gb_network.json"):
    """Branches in API order with route coordinates recovered as lon/lat."""
    network = json.loads(Path(path).read_text())
    lon = np.array([b["lon"] for b in network["buses"]])
    lat = np.array([b["lat"] for b in network["buses"]])
    x0, y0, y1 = lon.min(), lat.min(), lat.max()
    # Inverse of scripts/build_gb_network.py's equirectangular projection.
    to_lonlat = lambda p: (p[0] * (y1 - y0) / math.cos(math.radians(54)) + x0, y1 - p[1] * (y1 - y0))
    lines = []
    for e in network["branches"]:
        lines.append({**{k: e[k] for k in ["id", "kv", "length_km", "ele_mean_m", "ele_max_m", "circuits",
                                           "pylons", "span_m", "tower_height_m"]},
                      "route": [to_lonlat(p) for p in e["geometry"]]})
    return lines


def route_links(line):
    """Route split into ~2 km pieces: weather cell, length fraction and bearings per cell."""
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    rows = []
    for (ax, ay), (bx, by) in zip(line["route"], line["route"][1:]):
        n = max(1, int(math.hypot(bx - ax, by - ay) / SEGMENT_DEG))
        for k in range(n):
            px, py = ax + (bx - ax) * k / n, ay + (by - ay) * k / n
            qx, qy = ax + (bx - ax) * (k + 1) / n, ay + (by - ay) * (k + 1) / n
            azimuth, _, length = geod.inv(px, py, qx, qy)
            if length > 0:
                rows.append((cell_key((py + qy) / 2, (px + qx) / 2), azimuth % 180, length))
    d = pd.DataFrame(rows, columns=["weather_key", "bearing", "length_m"])
    links = d.groupby("weather_key").agg(length_m=("length_m", "sum"), bearings=("bearing", list)).reset_index()
    links["length_fraction"] = links.length_m / links.length_m.sum()
    return links


def _bng():
    from pyproj import Transformer
    return Transformer.from_crs(4326, 27700, always_xy=True).transform


def route_geometry(line, to_bng):
    import shapely
    return shapely.LineString([to_bng(*p) for p in line["route"]])


def ssen_territory(to_bng):
    """Union of SSEN primary supply areas (SEPD and SHEPD), in British National Grid."""
    import shapely
    from shapely.ops import transform
    areas = []
    for path in glob.glob(str(ROOT / "data/processed/primary-supply-areas_*.parquet")):
        areas += list(shapely.from_wkt(pd.read_parquet(path, columns=["geometry"]).geometry.to_numpy()))
    return transform(to_bng, shapely.union_all(areas)).buffer(0)


def vegetation_fractions(lines, to_bng):
    """Share of route length within 30 m of NFI 2024 woodland; NaN where NFI was not downloaded."""
    import shapely
    envelopes = []
    for name in ["woodland_manifest.json", "woodland_extensions_manifest.json"]:
        for record in json.loads((ROOT / "data" / name).read_text()):
            box = tuple(map(float, record["params"]["geometry"].split(",")))
            envelopes.append(shapely.box(*box))
    covered = shapely.union_all(envelopes)
    woods = pd.read_parquet(ROOT / "data/processed/woodland_2024.parquet", columns=["geometry_bng_wkb"])
    polygons = shapely.from_wkb(woods.geometry_bng_wkb.to_numpy())
    tree = shapely.STRtree(polygons)
    result = {}
    for line in lines:
        route = route_geometry(line, to_bng)
        if not covered.contains(route):
            result[line["id"]] = np.nan
            continue
        near = polygons[tree.query(route, predicate="dwithin", distance=WOODLAND_BUFFER_M)]
        if not len(near):
            result[line["id"]] = 0.
            continue
        zone = shapely.union_all(near).buffer(WOODLAND_BUFFER_M)
        result[line["id"]] = float(route.intersection(zone).length / route.length)
    return result


def corridors(lines, to_bng, territory):
    import shapely
    rows = []
    for line in lines:
        corridor = route_geometry(line, to_bng).buffer(RADIUS_M)
        rows.append({"asset_id": line["id"], "corridor": corridor,
                     "ssen_fraction": float(corridor.intersection(territory).area / corridor.area)})
    return pd.DataFrame(rows)


def incidents():
    """Weather-caused SSEN overhead-line reports, deduplicated into incidents."""
    o = pd.read_parquet(ROOT / "data/grid/normalized/all_outage_reports.parquet",
                        columns=["event_key", "operator", "reported_at_utc", "network_reference", "longitude",
                                 "latitude", "cause", "equipment", "report_class", "reported_voltage_kv"])
    pattern = re.compile("^(" + "|".join(re.escape(c) for c in WEATHER_CAUSES) + ")", re.I)
    s = o[o.operator.isin(["SEPD", "SHEPD"]) & o.equipment.eq("Overhead Lines")
          & o.cause.fillna("").str.match(pattern) & o.report_class.ne("possible_operational_or_planned")].copy()
    s["reported_utc"] = pd.to_datetime(s.reported_at_utc, utc=True, format="ISO8601", errors="coerce")
    report = {"weather_overhead_reports": len(s), "unresolved_time": int(s.reported_utc.isna().sum()),
              "unlocated": int(s.longitude.isna().sum())}
    s = deduplicate(s.dropna(subset=["reported_utc", "longitude", "latitude"]))
    report["incidents_after_dedup"] = len(s)
    return s, report


def deduplicate(reports):
    """Repeated reports for one feeder within 6 h are one incident; keep the first as onset proxy."""
    s = reports.sort_values("reported_utc").copy()
    s["key"] = s.operator + ":" + s.network_reference.fillna(s.event_key)
    gap = s.groupby("key").reported_utc.diff()
    s = s[gap.isna() | gap.gt(pd.Timedelta(hours=DEDUP_HOURS))].copy()
    s["onset_hour"] = s.reported_utc.dt.floor("h")
    return s.reset_index(drop=True)


def assign_incidents(events, corridor_table, to_bng):
    import shapely
    x, y = to_bng(events.longitude.to_numpy(), events.latitude.to_numpy())
    tree = shapely.STRtree(shapely.points(x, y))
    hits = {}
    for row in corridor_table.itertuples():
        hits[row.asset_id] = events.onset_hour.iloc[tree.query(row.corridor, predicate="contains")]
    return hits


def route_weather(links, weather, keys):
    """Per-stamp route summaries from cell series (time x cell arrays)."""
    cols = [keys.index(k) for k in links.weather_key]
    w = links.length_fraction.to_numpy()
    gust = weather["wind_gusts_10m"][:, cols]
    direction = weather["wind_direction_10m"][:, cols]
    cross = np.full(gust.shape, np.nan)
    for j, bearings in enumerate(links.bearings):
        theta = direction[:, j]
        ok = np.isfinite(theta)
        envelope = crosswind_envelope(bearings)
        cross[ok, j] = gust[ok, j] * envelope[np.mod(np.rint(theta[ok]).astype(int), 360)]
    with np.errstate(all="ignore"):
        return {"gust_max": gust.max(axis=1), "gust_p95": weighted_percentile(gust.copy(), w),
                "crosswind_max": cross.max(axis=1),
                "rain": weather["precipitation"][:, cols] @ w, "temperature": weather["temperature_2m"][:, cols] @ w}


def line_features(links, weather, keys, stamps, hours):
    """Features for intervals [t, t+1h) with t in hours; stamps is the weather index.

    Open-Meteo/ERA5 gust and precipitation at stamp s cover the preceding hour, so the
    prediction interval [t, t+1) uses stamp t+1. Direction and temperature are the
    instantaneous values at t+1. History windows end at t (no overlap with the interval).
    """
    r = route_weather(links, weather, keys)
    position = stamps.get_indexer(hours + pd.Timedelta(hours=1))
    if (position < 24).any():
        raise ValueError("Weather index must start at least 24 hours before the first interval")
    gust_max = pd.Series(r["gust_max"])
    rain = pd.Series(r["rain"])
    prev3 = gust_max.rolling(3, min_periods=3).max().shift(1).to_numpy()  # stamps t-2..t
    prev24 = rain.rolling(24, min_periods=24).sum().shift(1).to_numpy()  # stamps t-23..t
    return {"gust_max_kmh": r["gust_max"][position], "gust_p95_kmh": r["gust_p95"][position],
            "gust_prev_3h_max_kmh": prev3[position], "crosswind_max_kmh": r["crosswind_max"][position],
            "rain_1h_mm": r["rain"][position], "rain_prev_24h_mm": prev24[position],
            "temperature_c": r["temperature"][position]}


def storm_windows(route_max, hours, threshold=100., merge_hours=48, shoulder_hours=24):
    """Group hours by weather: any training route gust >= threshold, merged and padded.

    Exploratory severe-wind clusters, not an official named-storm catalogue.
    """
    peak = np.nanmax(route_max, axis=1)
    position = np.flatnonzero(peak >= threshold)
    active = hours[position]
    rows = []
    if len(active):
        breaks = np.flatnonzero(np.diff(active.asi8) > merge_hours * 3600e9)
        for group in np.split(np.arange(len(active)), breaks + 1):
            start, end = active[group[0]], active[group[-1]]
            rows.append({"storm_id": "wind-" + start.strftime("%Y%m%d"),
                         "start_utc": start - pd.Timedelta(hours=shoulder_hours),
                         "end_utc": end + pd.Timedelta(hours=shoulder_hours + 1),
                         "peak_route_gust_kmh": float(peak[position[group]].max())})
    return pd.DataFrame(rows, columns=["storm_id", "start_utc", "end_utc", "peak_route_gust_kmh"])


def storm_ids(hours, windows):
    ids = np.array(["quiet-" + h for h in hours.strftime("%Y-%m")], dtype=object)
    for w in windows.itertuples():
        ids[(hours >= w.start_utc) & (hours < w.end_utc)] = w.storm_id
    return ids


def build_dataset(output=OUTPUT):
    """Write one row per training-domain line x hour with features and failed_next_hour."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    to_bng = _bng()
    lines = load_network()
    territory = ssen_territory(to_bng)
    table = corridors(lines, to_bng, territory)
    vegetation = vegetation_fractions(lines, to_bng)
    links = {line["id"]: route_links(line) for line in lines}
    domain = table[table.ssen_fraction >= TERRITORY_MIN].asset_id.tolist()
    keys = sorted({k for i in domain for k in links[i].weather_key})
    missing = [k for k in keys if not (ROOT / "data/processed/weather" / f"{k}.parquet").exists()]
    if missing:
        raise ValueError(f"Training-domain weather cells missing from the archive: {missing}")
    events, event_report = incidents()
    hits = assign_incidents(events, table[table.asset_id.isin(domain)], to_bng)

    from .gb_weather import load
    hours = pd.date_range(COVERAGE[0] + pd.Timedelta(hours=24), COVERAGE[1], freq="h", inclusive="left")
    stamps = pd.date_range(COVERAGE[0], COVERAGE[1] + pd.Timedelta(hours=1), freq="h", inclusive="left")
    weather = load(keys, stamps)
    statics = {line["id"]: line for line in lines}
    frames, route_max = [], []
    for asset in domain:
        f = line_features(links[asset], weather, keys, stamps, hours)
        route_max.append(f["gust_max_kmh"])
        target = np.zeros(len(hours), dtype=np.int8)
        target[hours.get_indexer(pd.DatetimeIndex(hits[asset].unique()).intersection(hours))] = 1
        frame = pd.DataFrame({"asset_id": asset, "timestamp_utc": hours,
                              **{k: v.astype(np.float32) for k, v in f.items()}})
        for col in ASSET_FEATURES + DERIVED:
            frame[col] = np.float32(statics[asset][col])
        frame["vegetation_fraction"] = np.float32(vegetation[asset])
        frame["failed_next_hour"] = target
        frames.append(frame)
    windows = storm_windows(np.column_stack(route_max), hours)
    rows = pd.concat(frames, ignore_index=True)
    rows["storm_id"] = np.tile(storm_ids(hours, windows), len(domain))
    if rows[WEATHER_FEATURES].isna().any().any():
        raise ValueError("Training rows have missing route weather")
    rows.to_parquet(output / "rows.parquet", index=False, compression="zstd", row_group_size=500000)
    windows.to_parquet(output / "storm_windows.parquet", index=False)
    lines_table = table.drop(columns="corridor").assign(
        training_domain=lambda d: d.ssen_fraction >= TERRITORY_MIN,
        vegetation_fraction=lambda d: d.asset_id.map(vegetation),
        weather_cells=lambda d: d.asset_id.map(lambda i: ",".join(links[i].weather_key)),
        incidents_in_corridor=lambda d: d.asset_id.map(lambda i: len(hits.get(i, []))))
    lines_table.to_csv(output / "lines.csv", index=False)
    report = {"generated_utc": now(), "rows": len(rows), "assets": len(domain),
              "positive_rows": int(rows.failed_next_hour.sum()), "event_frequency": float(rows.failed_next_hour.mean()),
              "start_utc": hours[0].isoformat(), "end_utc_exclusive": (hours[-1] + pd.Timedelta(hours=1)).isoformat(),
              "corridor_radius_m": RADIUS_M, "territory_min_fraction": TERRITORY_MIN, "dedup_hours": DEDUP_HOURS,
              "weather_causes": WEATHER_CAUSES, "weather_cells": keys, "storm_windows": len(windows),
              **event_report,
              "target": "new weather-caused SSEN overhead-line fault report inside the 5 km corridor during [t,t+1h)",
              "gust_convention": "raw ERA5 10 m gust via Open-Meteo, maximum of the preceding hour; no terrain adjustment",
              "interval_alignment": "interval [t,t+1h) uses stamp t+1 for gust/rain/direction/temperature; histories end at t",
              "availability": "no transmission availability records exist; no hours excluded as unavailable"}
    write_json(output / "dataset_report.json", report)
    return report
