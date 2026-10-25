"""On-demand hourly ERA5 for GB line routes, using the regional archive's exact request.

The archive in data/processed/weather holds 87 full-period (2015-04-01..2026-10-18)
0.5-degree cells around the two SSEN licence areas. GB routes outside them are
fetched for the requested window only, from the same Open-Meteo ERA5 endpoint with
the same variables, so training and inference share one weather convention:
raw 10 m weather-grid gust (maximum of the preceding hour), no terrain adjustment.
"""
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from .common import ROOT, now, write_json

API = "https://archive-api.open-meteo.com/v1/archive"
# Identical to data/weather_config.json, so window downloads match the archive cells.
VARIABLES = ("wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,"
             "precipitation,snow_depth,soil_moisture_0_to_7cm")
COLUMNS = ["wind_gusts_10m", "wind_direction_10m", "temperature_2m", "precipitation"]
HISTORY = ROOT / "data/processed/weather"
CACHE = ROOT / "data/failure_model/gb_weather"
BATCH = 20  # locations per request


class RateLimited(RuntimeError):
    pass


def _get(params, attempts=6):
    for attempt in range(attempts):
        response = requests.get(API, params=params, timeout=(10, 180))
        if response.status_code != 429:
            response.raise_for_status()
            return response
        # Minutely/hourly quotas clear; a daily quota does not within a run.
        if "daily" in response.text.lower():
            raise RateLimited(response.text[:300])
        time.sleep(30 * (attempt + 1))
    raise RateLimited(response.text[:300])


def fetch_window(keys, start_date, end_date):
    """Download [start_date, end_date] (inclusive UTC dates) for cells not yet cached."""
    folder = CACHE / f"{start_date}_{end_date}"
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    missing = [k for k in sorted(set(keys)) if not (folder / f"{k}.parquet").exists()]
    for i in range(0, len(missing), BATCH):
        chunk = missing[i:i + BATCH]
        lats, lons = zip(*(k.split("_") for k in chunk))
        params = {"latitude": ",".join(lats), "longitude": ",".join(lons), "start_date": start_date,
                  "end_date": end_date, "hourly": VARIABLES, "models": "era5", "timezone": "UTC",
                  "wind_speed_unit": "kmh"}
        body = _get(params).text
        payload = json.loads(body)
        payload = payload if isinstance(payload, list) else [payload]
        if len(payload) != len(chunk):
            raise ValueError("Open-Meteo returned a different number of locations than requested")
        for key, item in zip(chunk, payload):
            d = pd.DataFrame(item["hourly"])
            d["time"] = pd.to_datetime(d.time, utc=True)
            raw = json.dumps(item, separators=(",", ":"))
            (folder / f"{key}.json").write_text(raw)
            d.to_parquet(folder / f"{key}.parquet", index=False)
            manifest.append({"weather_key": key, "source": API, "model": "era5", "variables": VARIABLES,
                             "start_date": start_date, "end_date": end_date, "retrieved_utc": now(),
                             "provider_latitude": item["latitude"], "provider_longitude": item["longitude"],
                             "provider_elevation_m": item.get("elevation"), "units": item["hourly_units"],
                             "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(), "rows": len(d),
                             "license": "CC-BY-4.0 (Open-Meteo; ERA5 Copernicus)"})
        write_json(manifest_path, manifest)
        print(f"ERA5 window {start_date}..{end_date}: {min(i + BATCH, len(missing))}/{len(missing)} new cells", flush=True)
    return folder


def load(keys, index, folder=None):
    """Hourly arrays (time x cell) for an aligned UTC index; missing hours stay NaN.

    Uses the window cache when given, otherwise the full-period archive cells.
    """
    keys = list(keys)
    out = {c: np.full((len(index), len(keys)), np.nan, dtype=np.float64) for c in COLUMNS}
    for j, key in enumerate(keys):
        path = (Path(folder) if folder else HISTORY) / f"{key}.parquet"
        if not path.exists():
            continue
        d = pd.read_parquet(path, columns=["time", *COLUMNS])
        d = d.drop_duplicates("time").set_index("time").reindex(index)
        for c in COLUMNS:
            out[c][:, j] = d[c].to_numpy(dtype=float)
    return out
