"""Compare candidate weather sources with independent station gust observations.

Sources:
- NOAA ISD/Global Hourly station gusts (OC1), data/grid/raw/research/weather_observations:
  the independent observations, nine stations in southern Scotland, northern England and NI.
- ERA5 via Open-Meteo at the 0.5-degree lattice cell the line model would use, and at
  the station point itself (nearest native 0.25-degree cell). Fetched on demand.
- Met Office UKV (2 km) via Open-Meteo from the workspace storm files (data/storms),
  gusts only, available for the named storm windows that were built.

Each source is aligned as the maximum gust of the hour ending at the stamp.
Writes data/failure_model/weather_audit.json.
"""
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from failure_model.common import write_json
from failure_model.gb_weather import fetch_window, load
from failure_model.inventory import cell_key

OBS = ROOT / "data/grid/raw/research/weather_observations"
STORMS = {"arwen-2021": ("2021-11-26T00:00Z", 48, None),
          "eowyn-2025": ("2025-01-23T12:00Z", 48, ROOT / "data/storms/eowyn-2025.json")}
GOOD_QUALITY = set("0145")


def station_gusts(year, usaf):
    d = pd.read_csv(OBS / f"{year}_{usaf}99999.csv", low_memory=False)
    if "OC1" not in d:  # this station reported no gusts that year
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    d = d[["DATE", "OC1"]].dropna()
    parts = d.OC1.str.split(",", expand=True)
    speed = pd.to_numeric(parts[0], errors="coerce")
    ok = parts[1].isin(GOOD_QUALITY) & speed.lt(9999)
    t = pd.to_datetime(d.DATE[ok], utc=True)
    s = pd.Series(speed[ok].to_numpy() * 0.36, index=t)  # m/s x10 -> km/h
    # Max over (stamp-1h, stamp], matching the preceding-hour convention.
    return s.groupby(s.index.ceil("h")).max()


def ukv_series(path, lat, lon, index):
    storm = json.loads(Path(path).read_text())
    grid = storm["grid"]
    j = int(np.argmin(np.abs(np.array(grid["lats"]) - lat)))
    i = int(np.argmin(np.abs(np.array(grid["lons"]) - lon)))
    stamps = pd.to_datetime(storm["timestamps"], utc=True)
    values = [np.nan if h[j][i] is None else h[j][i] for h in grid["gust_kmh"]]
    return pd.Series(values, index=stamps).reindex(index), (grid["lats"][j], grid["lons"][i])


def compare(obs, model):
    both = pd.concat([obs, model], axis=1, keys=["obs", "model"]).dropna()
    if len(both) < 6:
        return None
    diff = both.model - both.obs
    return {"hours": len(both), "bias_kmh": float(diff.mean()), "mae_kmh": float(diff.abs().mean()),
            "correlation": float(both.corr().iloc[0, 1]), "obs_peak_kmh": float(both.obs.max()),
            "model_peak_kmh": float(both.model.max()), "peak_ratio": float(both.model.max() / both.obs.max()),
            "peak_hour_offset": int((both.model.idxmax() - both.obs.idxmax()) / pd.Timedelta(hours=1))}


def main():
    stations = pd.read_csv(OBS / "selected_stations.csv", dtype={"USAF": str})
    results = {}
    for storm, (start, hours, ukv_path) in STORMS.items():
        index = pd.date_range(start, periods=hours, freq="h", tz=None)
        index = index.tz_convert("UTC") if index.tz else index.tz_localize("UTC")
        lattice = {r.USAF: cell_key(r.LAT, r.LON) for r in stations.itertuples()}
        exact = {r.USAF: f"{r.LAT:.3f}_{r.LON:.3f}" for r in stations.itertuples()}
        keys = sorted(set(lattice.values()) | set(exact.values()))
        folder = fetch_window(keys, (index[0] - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                              (index[-1] + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
        era5 = load(keys, index, folder)["wind_gusts_10m"]
        rows = []
        for r in stations.itertuples():
            obs = station_gusts(index[0].year, r.USAF).reindex(index)
            if obs.notna().sum() < 6:
                continue
            row = {"station": r._3, "usaf": r.USAF, "elevation_m": r._9}
            row["era5_lattice"] = compare(obs, pd.Series(era5[:, keys.index(lattice[r.USAF])], index=index))
            row["era5_point"] = compare(obs, pd.Series(era5[:, keys.index(exact[r.USAF])], index=index))
            if ukv_path and ukv_path.exists():
                series, at = ukv_series(ukv_path, r.LAT, r.LON, index)
                row["ukv_grid"] = compare(obs, series)
            rows.append(row)
        summary = {}
        for source in ["era5_lattice", "era5_point", "ukv_grid"]:
            values = [x[source] for x in rows if x.get(source)]
            if values:
                summary[source] = {k: float(np.mean([v[k] for v in values]))
                                   for k in ["bias_kmh", "mae_kmh", "correlation", "peak_ratio"]}
                summary[source]["stations"] = len(values)
        results[storm] = {"window_start": index[0].isoformat(), "hours": hours, "stations": rows, "mean_over_stations": summary}
    write_json(ROOT / "data/failure_model/weather_audit.json", results)
    for storm, r in results.items():
        print(storm)
        for source, s in r["mean_over_stations"].items():
            print(f"  {source:13s} stations={s['stations']} bias={s['bias_kmh']:+.1f} km/h MAE={s['mae_kmh']:.1f} "
                  f"r={s['correlation']:.2f} peak ratio={s['peak_ratio']:.2f}")


if __name__ == "__main__":
    main()
