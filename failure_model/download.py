"""Fetch only missing public weather cells, preserving manifest and raw provenance."""
import hashlib
import json
from pathlib import Path

import pandas as pd
import requests

from .common import ROOT, now, write_json
from .inventory import cell_key


def fetch_pending(output, include_roster=False):
    from scripts.weather import features, VARIABLES
    output = Path(output)
    pending = pd.read_parquet(output / "weather_requests_pending.parquet").weather_key.tolist()
    if include_roster:
        cross = pd.read_parquet(output / "roster_weather_crosswalk.parquet")
        pending += cross.loc[~cross.weather_available, "weather_key"].tolist()
    config = json.loads((ROOT / "data/weather_config.json").read_text())
    manifest_path = ROOT / "data/weather_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    results = []
    for key in sorted(set(pending)):
        lat, lon = map(float, key.split("_"))
        key = cell_key(lat, lon)
        destination = ROOT / "data/processed/weather" / f"{key}.parquet"
        if destination.exists():
            continue
        raw = ROOT / "data/raw/weather" / f"{key}_{config['start']}_{config['end']}.json"
        params = {"latitude": lat, "longitude": lon, "start_date": config["start"], "end_date": config["end"],
                  "hourly": VARIABLES, "models": config["model"], "timezone": "UTC", "wind_speed_unit": "kmh"}
        record = {"weather_key": key, "params": params, "source": "https://archive-api.open-meteo.com/v1/archive",
                  "path": str(raw.relative_to(ROOT)), "license": "CC-BY-4.0", "retrieved_utc": now()}
        try:
            if raw.exists():
                body = raw.read_text()
            else:
                response = requests.get(record["source"], params=params, timeout=(10, 120))
                if response.status_code == 429:
                    record.update(status="rate_limited", error=response.text[:500])
                    results.append(record)
                    break
                response.raise_for_status()
                body = response.text
            payload = json.loads(body)
            data = features(pd.DataFrame(payload["hourly"]))
            data["weather_key"] = key
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(body)
            data.to_parquet(destination, index=False)
            record.update(status="downloaded", sha256=hashlib.sha256(raw.read_bytes()).hexdigest(), rows=len(data),
                          latitude=payload["latitude"], longitude=payload["longitude"], elevation_m=payload["elevation"], units=payload["hourly_units"])
        except (requests.RequestException, ValueError, KeyError) as error:
            record.update(status="failed", error=str(error))
        results.append(record)
        print(key, record["status"], flush=True)
    manifest.extend(results)
    write_json(manifest_path, manifest)
    write_json(output / "weather_download_attempts.json", results)
    return results
