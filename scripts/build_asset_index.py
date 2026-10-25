"""Index the archive's located grid assets for the map's procedural level of detail.

Reads data/grid/uk_power_grid.sqlite (the dataRefininf grid archive) and writes
data/grid/asset_index.parquet: one row per substation, power station, embedded generator,
transformer, storage, converter, compensator and switchgear site, with a continuous
importance score. No zoom tiers are stored: the map asks /api/assets for the most important
assets in its current view (backend/assets.py), so detail appears as you zoom in.

Usage: python scripts/build_asset_index.py [data/grid/uk_power_grid.sqlite]
"""
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TYPES = ['generation', 'substation', 'transformer', 'storage', 'converter', 'compensator', 'switchgear', 'distributed_generator']
# Baseline importance by kind; voltage and capacity add to it continuously.
BASE = {'generation': 2.5, 'converter': 2.5, 'substation': 1.5, 'transformer': 1.2, 'storage': 1.2,
        'compensator': 1.0, 'switchgear': .8, 'distributed_generator': 0.}


# Fuel class from DUKES/REPD/OSM tags (first match wins), for synchronous vs variable generation.
FUEL_WORDS = [('wind_offshore', ['offshore']), ('wind_onshore', ['wind']), ('solar', ['solar', 'photovoltaic']),
              ('nuclear', ['nuclear']), ('pumped', ['pumped']), ('hydro', ['hydro']), ('storage', ['battery', 'storage']),
              ('gas', ['gas', 'ccgt', 'ocgt', 'methane']), ('coal', ['coal']), ('oil', ['oil', 'diesel']),
              ('waste', ['waste', 'efw', 'landfill']), ('bioenergy', ['bio', 'biomass', 'anaerobic', 'wood']),
              ('marine', ['tidal', 'wave'])]


def fuel(kind, properties):
    text = (properties or '').lower()
    for name, words in FUEL_WORDS:
        if name == 'wind_offshore' and 'wind' not in text: continue
        if any(w in text for w in words): return name
    return 'storage' if kind == 'storage' else 'other'


def importance(kind, kv, mw):
    """Continuous score: kind + log-scaled voltage (to 400 kV) + log-scaled capacity (to 2 GW)."""
    kv = np.nan_to_num(np.asarray(kv, float)); mw = np.nan_to_num(np.asarray(mw, float))
    return (np.vectorize(BASE.get)(kind) + 3*np.log1p(kv)/np.log1p(400) + 3*np.log1p(mw)/np.log1p(2000)).astype(np.float32)


def build(database):
    con = sqlite3.connect(f'file:{database}?mode=ro', uri=True)
    marks = ','.join('?'*len(TYPES))
    frame = pd.read_sql_query(f"""SELECT node_id, node_type, name, source, longitude, latitude, voltage_kv,
                                         COALESCE(generation_capacity_mw, capacity_mva) AS mw,
                                         CASE WHEN node_type IN ('generation', 'distributed_generator', 'storage') THEN properties_json END AS properties
                                  FROM nodes WHERE node_type IN ({marks}) AND longitude IS NOT NULL AND latitude IS NOT NULL""",
                              con, params=TYPES)
    frame['score'] = importance(frame.node_type, frame.voltage_kv, frame.mw)
    frame['fuel'] = [fuel(k, p) if p is not None else None for k, p in zip(frame.node_type, frame.properties)]
    frame = frame.drop(columns='properties')
    frame['kind'] = frame.node_type.map({t: i for i, t in enumerate(TYPES)}).astype(np.int8)
    for col in ['longitude', 'latitude', 'voltage_kv', 'mw']:
        frame[col] = frame[col].astype(np.float32)
    frame['name'] = frame.name.where(frame.name.str.len() > 0)
    return frame.sort_values('score', ascending=False, kind='stable').drop(columns='node_type').reset_index(drop=True)


if __name__ == '__main__':
    database = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT/'data/grid/uk_power_grid.sqlite'
    index = build(database)
    destination = ROOT/'data/grid/asset_index.parquet'
    index.to_parquet(destination, index=False, compression='zstd')
    counts = index.kind.map(dict(enumerate(TYPES))).value_counts().to_dict()
    print(f'{len(index):,} assets -> {destination.relative_to(ROOT)}; {counts}')
