"""Replay a real storm (measured-model gusts) on the grid and compare with real outages.

The outage record is distribution-network faults from the operators' feeds that covered
the storm, so it cannot validate transmission pylon counts. It can test whether the simulation's
wind-driven risk lands in the right hours and places:

- timing: modelled expected pylon collapses per hour (inside the feed's coverage)
  vs. real outage starts per hour;
- place: per 0.25 degree cell, storm peak gust vs. how far outages rose above that
  cell's normal daily rate (normal rate from the rest of each feed's record).

Storms are built by scripts/build_storm_benchmark.py into data/storms/; every storm file there is listed.
"""
import json
import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np

from .storm import GUSTINESS, altitude_factor, model_info, route_samples, simulate_gusts, planning_exposure

ROOT = Path(__file__).resolve().parents[1]


def _list_storms():
    """Every built storm on disk, oldest first. Storm files are local data (most are not
    in git), so the list is whatever scripts/build_storm_benchmark.py has written here."""
    storms = []
    for path in sorted((ROOT/'data/storms').glob('*.json')):
        try:
            s = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(s, dict) or 'outages' not in s or 'grid' not in s:
            continue  # not a storm file
        gusts = [g for hour in s['grid']['gust_kmh'] for row in hour for g in row if g is not None]
        storms.append({'id': s['id'], 'name': s['name'], 'start': s['start'], 'hours': s['hours'],
                       'file': str(path.relative_to(ROOT)), 'operators': s.get('operators', [s.get('operator')]),
                       'outages': len(s['outages']), 'peak_gust_kmh': round(max(gusts)) if gusts else None})
    return sorted(storms, key=lambda s: s['start'])


STORM_LIST = _list_storms()
STORMS = {s['id']: s['file'] for s in STORM_LIST}


@lru_cache(maxsize=4)
def load_storm(storm_id):
    return json.loads((ROOT/STORMS[storm_id]).read_text(encoding='utf-8'))


class Projection:
    """Same equirectangular projection the network builder used (derived from bus extents)."""
    def __init__(self, net):
        if net.name.startswith('view:'):
            # View networks are drawn in the backbone's map units (view_network.build), whatever their extent.
            from .simulation import Network
            net = Network('gb')
        lon = np.array([b['lon'] for b in net.buses]); lat = np.array([b['lat'] for b in net.buses])
        self.x0, self.y0, self.y1 = lon.min(), lat.min(), lat.max()
        self.k = np.cos(np.radians(54))/(self.y1-self.y0)

    def xy(self, lon, lat): return (np.asarray(lon)-self.x0)*self.k, (self.y1-np.asarray(lat))/(self.y1-self.y0)

    def lonlat(self, x, y): return np.asarray(x)/self.k+self.x0, self.y1-np.asarray(y)*(self.y1-self.y0)


def bilinear(grid, lons, lats, lon, lat):
    """Interpolate a (lat, lon) grid with NaN gaps; missing corners are dropped and weights renormalised."""
    step = lons[1]-lons[0]
    fi = np.clip((lon-lons[0])/step, 0, len(lons)-1); fj = np.clip((lat-lats[0])/step, 0, len(lats)-1)
    i = np.minimum(fi.astype(int), len(lons)-2); j = np.minimum(fj.astype(int), len(lats)-2); u, v = fi-i, fj-j
    total = np.zeros(lon.shape); weight = np.zeros(lon.shape)
    for di, dj, w in ((0, 0, (1-u)*(1-v)), (1, 0, u*(1-v)), (0, 1, (1-u)*v), (1, 1, u*v)):
        value = grid[np.minimum(j+dj, len(lats)-1), np.minimum(i+di, len(lons)-1)]
        ok = ~np.isnan(value); total += np.where(ok, value*w, 0); weight += np.where(ok, w, 0)
    return np.where(weight > 0, total/np.maximum(weight, 1e-12), np.nan)


def spearman(a, b):
    rank = lambda x: np.argsort(np.argsort(x)).astype(float)
    return float(np.corrcoef(rank(a), rank(b))[0, 1]) if len(a) > 2 else float('nan')


def run_real_storm(net, storm_id='darragh-2024', seed=1):
    storm = load_storm(storm_id)
    rng = np.random.default_rng(seed)
    proj = Projection(net)
    samples = route_samples(net)
    hours = storm['hours']
    g = storm['grid']; lons, lats = np.array(g['lons']), np.array(g['lats'])
    gust_grid = np.array([[[np.nan if v is None else v for v in row] for row in hour] for hour in g['gust_kmh']], float)
    model_ele = np.array([[np.nan if v is None else v for v in row] for row in g['model_elevation_m']], float)

    # Route samples: interpolate the weather model's gust, then correct for the difference between the
    # route's real terrain height and the (smoother) height the weather model assumed there.
    s_lon, s_lat = proj.lonlat(samples['xy'][:, 0], samples['xy'][:, 1])
    s_model_ele = np.nan_to_num(bilinear(model_ele, lons, lats, s_lon, s_lat), nan=0.)
    height = np.array([net.branches[i]['tower_height_m'] for i in samples['line']])
    terrain = altitude_factor(samples['ele'], height)/altitude_factor(np.maximum(s_model_ele, 0), height)
    sample_gust = np.nan_to_num(np.array([bilinear(gust_grid[t], lons, lats, s_lon, s_lat) for t in range(hours)]), nan=0.)
    gust_at = lambda t: sample_gust[t]*terrain*rng.lognormal(0, GUSTINESS, len(s_lon))

    frames, hazard, hardened = simulate_gusts(net, hours, gust_at, rng)

    # Coverage: route samples inside cells where the outage feed has ever reported anything.
    size = storm['coverage_cell']; covered_cells = {tuple(c) for c in storm['coverage']}
    covered = np.array([(int(np.floor(a/size)), int(np.floor(b/size))) in covered_cells for a, b in zip(s_lon, s_lat)])
    outages = np.array(storm['outages'], dtype=object)
    observed = np.bincount(np.array([o[2] for o in storm['outages']], int), minlength=hours)[:hours]
    modelled = hazard[:, covered].sum(axis=1)

    # Place: cells with a meaningful normal rate; storm excess vs peak gust at the cell centre.
    step = storm['baseline']['cell']; days = hours/24
    # Normal rate per cell, summed over the feeds that covered the storm (each over its own span).
    base, expected_normal = {}, {}
    for feed in storm['baseline']['feeds']:
        for cx, cy, n in feed['counts']:
            base[(cx, cy)] = base.get((cx, cy), 0)+n
            expected_normal[(cx, cy)] = expected_normal.get((cx, cy), 0.)+n/feed['days']*days
    count = {}
    for lon, lat, *_ in storm['outages']:
        key = (int(np.floor(lon/step)), int(np.floor(lat/step))); count[key] = count.get(key, 0)+1
    with np.errstate(invalid='ignore'), warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)  # sea-only grid nodes are all-NaN
        peak = np.nanmax(gust_grid, axis=0)
    cells = []
    for (cx, cy), n in base.items():
        expected = expected_normal[(cx, cy)]
        if n < 20: continue  # too few normal-day outages to judge a rise
        c_lon, c_lat = (cx+.5)*step, (cy+.5)*step
        gust = bilinear(peak, lons, lats, np.array([c_lon]), np.array([c_lat]))[0]
        if np.isnan(gust): continue
        x, y = proj.xy(c_lon, c_lat)
        cells.append({'x': round(float(x), 5), 'y': round(float(y), 5), 'peak_gust_kmh': round(float(gust), 1),
                      'outages': count.get((cx, cy), 0), 'expected_normal': round(expected, 2),
                      'rise': round((count.get((cx, cy), 0)+.5)/(expected+.5), 2)})
    by_gust = sorted(cells, key=lambda c: c['peak_gust_kmh']); third = max(1, len(by_gust)//3)
    pooled = lambda cs: round(sum(c['outages'] for c in cs)/max(1e-9, sum(c['expected_normal'] for c in cs)), 1)

    # Display: weather nodes and outages in the network's map coordinates.
    nodes = [(j, i) for j in range(len(lats)) for i in range(len(lons)) if not np.isnan(gust_grid[0, j, i])]
    nx, ny = proj.xy([lons[i] for _, i in nodes], [lats[j] for j, _ in nodes])
    ox, oy = proj.xy([o[0] for o in storm['outages']], [o[1] for o in storm['outages']])
    cell_w, cell_h = step*proj.k, step/(proj.y1-proj.y0)
    cov_x, cov_y = proj.xy([(c[0]+.5)*size for c in storm['coverage']], [(c[1]+.5)*size for c in storm['coverage']])
    return {'config': {'scenario': storm_id, 'hours': hours, 'seed': seed}, 'model': model_info(net), 'frames': frames,
            'planning_weather':planning_exposure(net,frames,hazard,storm['timestamps'],
                f"Historical {storm['name']} weather replay; Met Office UKV via Open-Meteo; illustrative route/pylon fragility",hardened),
            'storm': {'id': storm_id, 'name': storm['name'], 'operator': storm['operator'], 'operators': storm['operators'], 'sources': storm['sources'],
                      'timestamps': storm['timestamps'], 'reissued_dropped': storm['reissued_dropped']},
            'weather': {'nodes': np.round(np.column_stack([nx, ny]), 5).tolist(), 'cell': [round(cell_w, 5), round(cell_h, 5)],
                        'gust_kmh': [[None if np.isnan(gust_grid[t, j, i]) else round(float(gust_grid[t, j, i])) for j, i in nodes]
                                     for t in range(hours)]},
            'outages': [[round(float(a), 5), round(float(b), 5), int(o[2])] for a, b, o in zip(ox, oy, outages)],
            'coverage': {'cells': np.round(np.column_stack([cov_x, cov_y]), 5).tolist(),
                         'cell': [round(size*proj.k, 5), round(size/(proj.y1-proj.y0), 5)]},
            'benchmark': {'observed_per_hour': observed.tolist(), 'modelled_per_hour': [float(f'{v:.3g}') for v in modelled],  # tiny values: keep significant figures
                          'timing_correlation': round(float(np.corrcoef(observed, modelled)[0, 1]), 3),
                          'peak_hour_observed': int(np.argmax(observed)), 'peak_hour_modelled': int(np.argmax(modelled)),
                          'modelled_collapses_in_coverage': float(f'{modelled.sum():.3g}'),
                          'cells': cells, 'place_rank_correlation': round(spearman([c['peak_gust_kmh'] for c in cells], [c['rise'] for c in cells]), 3),
                          'rise_windiest_third': pooled(by_gust[-third:]), 'rise_calmest_third': pooled(by_gust[:third])}}
