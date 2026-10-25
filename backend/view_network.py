"""Simulation networks that follow the map's view: detail where you look, coarser clusters elsewhere.

The national 33-400 kV graph (scripts/build_detail_graph.py, ~13,000 buses) is too large to
simulate hourly in Monte Carlo. For a view, buses are ranked by importance (voltage, generation,
demand, substation site); those inside the view get a large bonus. The top VIEW_BUSES inside the
view and OUTSIDE_BUSES elsewhere are kept and every other bus is eliminated by exact DC network
reduction, lowest priority first:

- a dead end's demand and generation move to its neighbour (exact for DC flows elsewhere);
- a series bus is merged away: the two branches become one with the series reactance, the lower
  rating and both routes, so storm damage anywhere along it still counts;
- a junction of three or more branches merges into its electrically closest neighbour (the
  strongest branch); its other lines re-attach there. Kept buses therefore stand for small
  clusters with no impedance inside them, an approximation, but every branch stays a physical
  route with its own geometry, terrain samples, rating and storm exposure. (Exact star-mesh
  elimination was tried: it replaces most routes by thousands of weatherless equivalent links.)

There are no preset levels: zooming in shrinks the view, so lower-voltage buses and lines inside
it rise into the budget and become real buses and branches of the simulated network.
"""
import hashlib
import heapq
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT/'data/grid/detail_graph.json'
CACHE = ROOT/'data/view_networks'
VIEW_BUSES = 300
OUTSIDE_BUSES = 100
GRID = 10
IN_VIEW_BONUS = 100.
EQUIVALENT_RATING = 1e6
BASE_MVA = 100.


@lru_cache(maxsize=1)
def graph():
    if not GRAPH.exists(): return None
    data = json.loads(GRAPH.read_text())
    buses = data['buses']
    gen = np.zeros(len(buses))
    for g in data['generators']: gen[g['bus']] += g['max_mw']
    kv = np.array([b['kv'] for b in buses], float); load = np.array([b['load_mw'] for b in buses])
    site = np.array([b['site'] for b in buses])
    # Continuous importance: voltage, generation, demand, being a substation site.
    data['importance'] = 3*np.log1p(kv)/np.log1p(400)+3*np.log1p(gen)/np.log1p(2000)+1.5*np.log1p(load)/np.log1p(500)+site
    data['lonlat'] = np.array([[b['lon'], b['lat']] for b in buses])
    return data


def _spread_top(index, x, y, box, budget):
    """Most important `index` entries, spread over a GRID x GRID split of the box."""
    if len(index) <= budget: return list(index)
    x0, y0, x1, y1 = box
    cell = (np.clip(((x-x0)/max(x1-x0, 1e-9)*GRID).astype(int), 0, GRID-1)*GRID
            + np.clip(((y-y0)/max(y1-y0, 1e-9)*GRID).astype(int), 0, GRID-1))
    seen = defaultdict(int); rank = np.empty(len(index))
    for k in range(len(index)):  # index is in importance order
        rank[k] = seen[cell[k]]; seen[cell[k]] += 1
    return list(np.asarray(index)[np.argsort(rank, kind='stable')[:budget]])


def reduce(data, keep):
    """Eliminate every bus not in `keep`. Returns kept buses with their aggregated injections and
    the reduced branch list (physical, merged-series or equivalent)."""
    n = len(data['buses'])
    load = np.array([b['load_mw'] for b in data['buses']], float)
    fuels = [defaultdict(float) for _ in range(n)]
    for g in data['generators']:
        for f, mw in g['fuels'].items(): fuels[g['bus']][f] += mw
    branches = {}
    adj = defaultdict(set)
    for i, e in enumerate(data['branches']):
        branches[i] = {**e, 'b': BASE_MVA/e['x_pu'], 'members': [i]}
        adj[e['from']].add(i); adj[e['to']].add(i)
    next_id = len(branches)
    keep = set(keep)
    importance = data['importance']
    heap = [(min(len(adj[u]), 3), importance[u], u) for u in range(n) if u not in keep]
    heapq.heapify(heap)
    removed = set()

    def other(e, u): return e['to'] if e['from'] == u else e['from']

    def move(u, targets, weights):
        total = sum(weights)
        for t, w in zip(targets, weights):
            share = w/total if total > 0 else 1/len(targets)
            load[t] += load[u]*share
            for f, mw in fuels[u].items(): fuels[t][f] += mw*share
        load[u] = 0; fuels[u].clear()

    while heap:
        degree_class, _, u = heapq.heappop(heap)
        if u in removed: continue
        incident = [i for i in adj[u]]
        if min(len(incident), 3) != degree_class:
            heapq.heappush(heap, (min(len(incident), 3), importance[u], u)); continue
        removed.add(u)
        if not incident: continue
        neighbours = [other(branches[i], u) for i in incident]
        susceptance = [branches[i]['b'] for i in incident]
        if len(incident) == 1:
            move(u, neighbours, [1.])
        elif len(incident) == 2 and neighbours[0] != neighbours[1]:
            e1, e2 = branches[incident[0]], branches[incident[1]]
            move(u, neighbours, susceptance)
            physical = e1['kind'] != 'equivalent' and e2['kind'] != 'equivalent'
            merged = {'from': neighbours[0], 'to': neighbours[1], 'b': e1['b']*e2['b']/(e1['b']+e2['b']),
                      'kind': ('line' if 'line' in (e1['kind'], e2['kind']) else e1['kind']) if physical else 'equivalent',
                      'kv': max(e1['kv'], e2['kv']), 'members': e1['members']+e2['members']}
            if physical:
                merged.update(length_km=e1['length_km']+e2['length_km'], circuits=min(e1['circuits'], e2['circuits']),
                              rating_mw=min(e1['rating_mw'], e2['rating_mw']), pylons=e1['pylons']+e2['pylons'],
                              samples=e1['samples']+e2['samples'], name=e1['name'] or e2['name'],
                              span_m=max(e1['span_m'], e2['span_m']), tower_height_m=max(e1['tower_height_m'], e2['tower_height_m']),
                              fragility_median_kmh=min([m for m in (e1['fragility_median_kmh'], e2['fragility_median_kmh']) if m] or [None]),
                              geometry=(e1['geometry'] if e1['to'] == u else e1['geometry'][::-1]) +
                                       (e2['geometry'] if e2['from'] == u else e2['geometry'][::-1]),
                              inferred=bool(e1.get('inferred') or e2.get('inferred')),
                              rating_sized=bool(e1.get('rating_sized') or e2.get('rating_sized')))
                if neighbours[0] == merged['to']: merged['geometry'] = merged['geometry'][::-1]
            for i in incident:
                adj[other(branches[i], u)].discard(i); del branches[i]
            branches[next_id] = merged; adj[neighbours[0]].add(next_id); adj[neighbours[1]].add(next_id); next_id += 1
            continue
        else:
            # Junction: merge into the electrically closest neighbour (strongest branch). Its other
            # lines re-attach there, so every branch stays a physical route.
            target = neighbours[int(np.argmax(susceptance))]
            move(u, [target], [1.])
            for i in incident:
                e = branches[i]
                w = other(e, u)
                adj[w].discard(i)
                if w == target:
                    del branches[i]  # now inside the merged bus
                    continue
                if e['from'] == u: e['from'] = target
                else: e['to'] = target
                adj[target].add(i); adj[w].add(i)
            continue
        for i in incident:
            adj[other(branches[i], u)].discard(i); del branches[i]
    return load, fuels, list(branches.values())


def build(box, view_buses=VIEW_BUSES, outside_buses=OUTSIDE_BUSES):
    """Network dict (gb_network.json format, base map projection) for a view box in map units."""
    from .benchmark import Projection
    from .simulation import Network
    data = graph()
    if data is None: return None
    base = Network('gb')
    proj = Projection(base)
    lon, lat = data['lonlat'].T
    x, y = proj.xy(lon, lat)
    order = np.argsort(-data['importance'], kind='stable')
    x0, y0, x1, y1 = box
    inside = (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
    in_view = order[inside[order]]; outside = order[~inside[order]]
    keep = _spread_top(in_view, x[in_view], y[in_view], box, view_buses)
    keep += list(outside[:outside_buses])
    load, fuels, branches = reduce(data, keep)
    kept = sorted(set(keep)); index = {u: k for k, u in enumerate(kept)}
    xy = lambda pts: [list(map(lambda v: round(float(v), 5), proj.xy(p[0], p[1]))) for p in pts]
    buses = [{'id': k+1, 'load_mw': round(float(load[u]), 3), 'lon': data['buses'][u]['lon'], 'lat': data['buses'][u]['lat'],
              'x': round(float(x[u]), 5), 'y': round(float(y[u]), 5), 'kv': data['buses'][u]['kv'], 'ele_m': 0.,
              'name': data['buses'][u]['name'], 'in_view': bool(inside[u])} for k, u in enumerate(kept)]
    generators = []
    for u in kept:
        if fuels[u]:
            cap = sum(fuels[u].values())
            generators.append({'bus': index[u]+1, 'max_mw': round(cap, 2), 'base_mw': round(cap*.4, 2), 'fuels': {f: round(m, 2) for f, m in fuels[u].items()}})
    out_branches = []
    for e in branches:
        if e['from'] not in index or e['to'] not in index or e['from'] == e['to']: continue
        physical = e['kind'] != 'equivalent'
        key = hashlib.sha1(','.join(map(str, sorted(e['members']))).encode()).hexdigest()[:6] if physical else f"{min(e['from'], e['to'])}-{max(e['from'], e['to'])}"
        samples = e.get('samples', []) if physical else []
        heights = [s[2] for s in samples] or [0.]
        a, b = data['buses'][e['from']], data['buses'][e['to']]
        out_branches.append({
            'id': ('L' if e['kind'] == 'line' else {'cable': 'C', 'transformer': 'T', 'equivalent': 'E'}[e['kind']]) + key,
            'kind': e['kind'], 'from': index[e['from']]+1, 'to': index[e['to']]+1, 'x_pu': BASE_MVA/e['b'], 'tap': 1.,
            'kv': e['kv'], 'length_km': round(e.get('length_km', 0.), 2), 'circuits': e.get('circuits', 1),
            'rating_mw': e.get('rating_mw', EQUIVALENT_RATING), 'name': e.get('name'),
            'pylons': e.get('pylons', 0) if e['kind'] == 'line' else 0, 'span_m': e.get('span_m', 0), 'tower_height_m': e.get('tower_height_m', 0),
            'fragility_median_kmh': e.get('fragility_median_kmh'),
            'ele_max_m': round(max(heights), 1), 'ele_mean_m': round(sum(heights)/len(heights), 1),
            'samples': [xy([s])[0]+[s[2]] for s in samples] if e['kind'] == 'line' else [],
            'geometry': xy(e['geometry']) if physical else xy([[a['lon'], a['lat']], [b['lon'], b['lat']]]),
            'cost': 1 if e.get('length_km', 0) < 25 else 2 if e.get('length_km', 0) < 60 else 3,
            'exposure': round(min(.8, .05+.004*e.get('length_km', 0.)), 4) if e['kind'] == 'line' else 0.,
            **({'kv_low': e['kv_low']} if 'kv_low' in e else {}),
            **({'rating_sized': True} if e.get('rating_sized') else {}),
            **({'inferred': True} if e.get('inferred') else {})})
    # Unique IDs (merged routes can collide on the short hash).
    seen = defaultdict(int)
    for e in out_branches:
        seen[e['id']] += 1
        if seen[e['id']] > 1: e['id'] += f"_{seen[e['id']]}"
    network = {'source': 'National 33-400 kV OSM graph from the grid archive, reduced to this view (backend/view_network.py)',
               'base_mva': BASE_MVA, 'geographic': True, 'buses': buses, 'generators': generators, 'branches': out_branches,
               'view': {'box': [round(v, 5) for v in box], 'buses_in_view': int(sum(b['in_view'] for b in buses)),
                        'graph_buses_in_view': int(inside.sum()), 'graph_buses': len(data['buses'])},
               'notes': 'Detail inside the view, coarser clusters elsewhere; every branch is a mapped route. '
                        'Electrical parameters are illustrative per voltage.'}
    return network


def view_id(box):
    rounded = [round(v, 3) for v in box]
    return 'view:' + hashlib.sha1(json.dumps([rounded, VIEW_BUSES, OUTSIDE_BUSES, GRAPH.stat().st_mtime]).encode()).hexdigest()[:12]


SIZING_CASES = [('2026-01-01T18:00:00Z', 0.), ('2026-01-01T18:00:00Z', 60.), ('2026-01-01T04:00:00Z', 0.), ('2026-01-01T04:00:00Z', 60.)]


def size_view(name, margin=.85):
    """Clustering concentrates demand and drops parallel paths inside clusters, so a reduced network
    can overload where the full graph does not. Raise those ratings (never lower) so the intact view
    network carries the sizing cases within `margin`; the count is reported with the view."""
    from .simulation import Network
    from .grid_ops import demand_factor, solve
    path = path_for(name); data = json.loads(path.read_text())
    net = Network(name)
    peak = np.zeros(net.m)
    for stamp, gust in SIZING_CASES:
        demand = net.load*demand_factor([stamp], net.load.sum())[0]
        variable, firm, sync = net.grid_model.availability(np.full(net.m, gust), stamp)
        peak = np.maximum(peak, np.abs(solve(net, np.ones(net.m), demand, variable, firm, sync)['flows']))
    raised = 0
    for e, flow in zip(data['branches'], peak):
        if flow > margin*e['rating_mw']:
            e['rating_mw'] = round(flow/margin, 1); e['rating_sized'] = True; raised += 1
    data['view']['ratings_raised_for_view'] = raised
    path.write_text(json.dumps(data, separators=(',', ':')))
    return raised


def ensure(box):
    """Build (or reuse) the view network file and return its network name."""
    name = view_id(box)
    path = CACHE/f"{name.split(':')[1]}.json"
    if not path.exists():
        network = build(box)
        if network is None: return None
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(network, separators=(',', ':')))
        size_view(name)
    return name


def path_for(name):
    return CACHE/f"{name.split(':', 1)[1]}.json"
