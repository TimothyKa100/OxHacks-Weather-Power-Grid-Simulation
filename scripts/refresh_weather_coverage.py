"""Refresh derived coverage and the SQLite feeder-weather links without rebuilding the graph."""
from pathlib import Path
import sqlite3
import subprocess
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main():
    subprocess.run([sys.executable, str(ROOT / "scripts/finalize.py")], check=True, cwd=ROOT)
    subprocess.run([sys.executable, str(ROOT / "scripts/feature_snapshot.py")], check=True, cwd=ROOT)
    cross = pd.read_parquet(ROOT / "data/processed/roster_weather_crosswalk.parquet")
    path = ROOT / "data/grid/british_grid.sqlite"
    if path.exists():
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(existing_feeder_weather_links)")]
            if set(columns) != set(cross.columns):
                raise ValueError("SQLite weather-link schema changed; inspect before refresh")
            # Preserve the existing table/index schema and update only its derived rows.
            placeholders = ",".join("?" for _ in columns)
            rows = cross[columns].astype(object).where(cross[columns].notna(), None)
            db.execute("DELETE FROM existing_feeder_weather_links")
            db.executemany(f"INSERT INTO existing_feeder_weather_links VALUES ({placeholders})", rows.itertuples(index=False, name=None))
    print(f"Refreshed {len(cross):,} feeder links; {int(cross.weather_available.sum()):,} have downloaded weather")


if __name__ == "__main__":
    main()
