"""Grid-archive assets on the map: procedural level of detail and link to the simulation.

The cascade runs on the transmission backbone (data/gb_network.json). Every located archive
asset (substations, power stations, embedded generators, transformers, ...) is attached to the
backbone bus nearest to it, which stands in for the bus that supplies it: when the simulation
leaves that bus unsupplied, the asset is shown without supply. Northern Ireland is a separate
synchronous system and has no backbone here, so its assets are never linked.

There are no preset zoom levels. For any view, `select` returns the most important assets
inside it up to a budget (about 1,000), spread across the view: each asset's importance is
discounted by how many more important assets already share its part of the screen. Zooming in
shrinks the view, so less important assets rise into the budget.
"""
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT/'data/grid/asset_index.parquet'
KINDS = ['generation', 'substation', 'transformer', 'storage', 'converter', 'compensator', 'switchgear', 'distributed_generator']
GRID = 12              # view divided into GRID x GRID cells for spreading
SPREAD = 1.0           # importance lost per doubling of better assets already in the same cell
CANDIDATES = 60        # consider at most this many x budget, most important first


@lru_cache(maxsize=1)
def load(net):
    """Arrays in network map units, sorted by importance, with each asset's supplying bus."""
    import pandas as pd
    from scipy.spatial import cKDTree
    from .benchmark import Projection
    if not INDEX.exists():
        return None
    frame = pd.read_parquet(INDEX)
    proj = Projection(net)
    x, y = proj.xy(frame.longitude.to_numpy(), frame.latitude.to_numpy())
    buses = np.array([[b['x'], b['y']] for b in net.buses])
    distance, bus = cKDTree(buses).query(np.column_stack([x, y]))
    lon, lat = frame.longitude.to_numpy(), frame.latitude.to_numpy()
    northern_ireland = (lon < -5.35) & (lat > 54.0) & (lat < 55.4)
    bus = np.where(northern_ireland, -1, bus)
    return {'x': x.astype(np.float32), 'y': y.astype(np.float32), 'score': frame.score.to_numpy(np.float32),
            'kind': frame.kind.to_numpy(np.int8), 'kv': frame.voltage_kv.to_numpy(np.float32),
            'mw': frame.mw.to_numpy(np.float32), 'bus': bus.astype(np.int32), 'name': frame.name.to_numpy(object),
            'id': frame.node_id.to_numpy(object), 'source': frame.source.to_numpy(object)}


def select(index, x0, y0, x1, y1, budget=1000):
    """Indices of the assets to draw in the view [x0, x1] x [y0, y1]."""
    inside = np.flatnonzero((index['x'] >= x0) & (index['x'] <= x1) & (index['y'] >= y0) & (index['y'] <= y1))
    total = len(inside)
    if total <= budget:
        return inside, total
    pool = inside[:CANDIDATES*budget]  # the index is sorted by importance
    cx = np.clip(((index['x'][pool]-x0)/max(x1-x0, 1e-9)*GRID).astype(int), 0, GRID-1)
    cy = np.clip(((index['y'][pool]-y0)/max(y1-y0, 1e-9)*GRID).astype(int), 0, GRID-1)
    cell = cx*GRID+cy
    # Rank of each asset among more important ones in its cell (pool is already importance order).
    order = np.argsort(cell, kind='stable')
    sorted_cell = cell[order]
    starts = np.flatnonzero(np.r_[True, sorted_cell[1:] != sorted_cell[:-1]])
    run = np.repeat(starts, np.diff(np.r_[starts, len(order)]))
    rank = np.empty(len(pool), int); rank[order] = np.arange(len(pool))-run
    priority = index['score'][pool]-SPREAD*np.log2(1+rank)
    chosen = pool[np.argsort(-priority, kind='stable')[:budget]]
    return chosen, total


def payload(index, chosen, total):
    def number(v): return None if not np.isfinite(v) else round(float(v), 1)
    return {'total_in_view': int(total), 'shown': int(len(chosen)), 'kinds': KINDS,
            'assets': [[round(float(index['x'][i]), 5), round(float(index['y'][i]), 5), int(index['kind'][i]),
                        round(float(index['score'][i]), 2), number(index['kv'][i]), number(index['mw'][i]),
                        int(index['bus'][i]), index['name'][i] if isinstance(index['name'][i], str) else None,
                        index['id'][i]] for i in chosen]}
