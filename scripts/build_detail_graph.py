"""Build the national 33-400 kV grid graph that view networks are reduced from.

Source: data/grid/uk_power_grid.sqlite (the dataRefininf grid archive), OSM layer, Great Britain.
- Branches: OSM line/cable sections at 33-400 kV (AC), between their mapped terminal junctions.
- Buses: a junction inside a substation or power-station footprint becomes that site's bus for its
  voltage (one bus per voltage level per site); other junctions are buses of their own. Series
  junctions of one voltage are contracted.
- Transformers join the voltage levels inside a site. Sites that hold only one voltage but sit
  within TRANSFORMER_KM of a different-voltage site are joined too (separately mapped footprints).
- Route samples every ~2 km carry terrain height for the storm model.
- Demand follows the backbone's demand map (data/gb_network.json): each backbone bus's demand is
  shared among the supply-point buses (33-132 kV substations) nearest to it. Generation comes from
  the asset index (scripts/build_asset_index.py) with fuel classes, snapped to the nearest bus.
Electrical parameters are illustrative per voltage, like the backbone's.

Usage: python scripts/build_detail_graph.py   (writes data/grid/detail_graph.json)
"""
from collections import defaultdict
import json
import math
from pathlib import Path
import sqlite3
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_gb_network import (CABLE_RATING_MW, CABLE_X_OHM_KM, CAPACITY_FACTOR, RATING_MW, SPAN_M, TOWER_HEIGHT_M,
                                      X_OHM_KM, DSU, km, simplify)

LEVELS = [33, 66, 132, 275, 400]


def osm_node(terminal):
    """Underlying OSM node of a terminal: lines meeting at one node share it, whether the archive
    calls it a junction ('OSM:junction:<node>:voltage:<kv>') or a route end ('...route_port:way/<w>:<node>')."""
    if ':junction:' in terminal: return 'n' + terminal.split(':junction:')[1].split(':')[0]
    if ':route_port:' in terminal: return 'n' + terminal.rsplit(':', 1)[1]
    return terminal
X_OHM_KM_ALL = {**X_OHM_KM, 66: .40, 33: .38}
RATING_ALL = {**RATING_MW, 66: 60., 33: 30.}
CABLE_X_ALL = {**CABLE_X_OHM_KM, 132: .15, 66: .15, 33: .12}
CABLE_RATING_ALL = {**CABLE_RATING_MW, 132: 150., 66: 50., 33: 25.}
SPAN_ALL = {**SPAN_M, 66: 150., 33: 100.}
HEIGHT_ALL = {**TOWER_HEIGHT_M, 66: 15., 33: 12.}
# Wood-pole 33/66 kV lines are weaker than lattice towers (illustrative fragility medians, km/h).
FRAGILITY_MEDIAN = {400: 265., 275: 265., 132: 250., 66: 200., 33: 190.}
TRANSFORMER_MW = {(275, 400): 1000., (132, 400): 240., (132, 275): 240., (66, 132): 90., (33, 132): 90., (33, 66): 45.,
                  (66, 275): 240., (66, 400): 240., (33, 275): 120., (33, 400): 120.}
TRANSFORMER_KM = 1.0
SNAP_KM = 0.5
JOIN_FRAGMENT_KM = 5.0
SAMPLE_KM = 2.0
BASE_MVA = 100.


def level(kv):
    return min(LEVELS, key=lambda v: abs(v-kv))


def fetch(con, sql, params=()):
    return con.execute(sql, params).fetchall()


def build(database=ROOT/'data/grid/uk_power_grid.sqlite'):
    import shapely
    con = sqlite3.connect(f'file:{database}?mode=ro', uri=True)
    sections = fetch(con, """SELECT node_id, voltage_kv, length_km, name, geometry_wgs84_wkb, properties_json FROM nodes
                             WHERE source = 'OSM' AND node_type = 'power_line' AND voltage_kv BETWEEN 30 AND 420""")
    print(f'{len(sections):,} OSM sections at 33-400 kV', flush=True)
    ids = [s[0] for s in sections]
    terminals = defaultdict(list)
    for chunk in range(0, len(ids), 900):
        part = ids[chunk:chunk+900]
        for a, b in fetch(con, f"SELECT from_node, to_node FROM edges WHERE relation = 'line_terminal' AND from_node IN ({','.join('?'*len(part))})", part):
            terminals[a].append(b)
    site_of = {}
    for site, junction, kind in fetch(con, """SELECT r.from_node, r.to_node, s.node_type FROM relationships r JOIN nodes s ON s.node_id = r.from_node
                                         WHERE r.source = 'OSM' AND r.relation IN ('within_site_footprint', 'supports_or_contains_terminal')
                                         AND s.node_type IN ('substation', 'generation')"""):
        key = osm_node(junction)
        if kind == 'substation' or key not in site_of: site_of[key] = site
    junctions = {t for ts in terminals.values() for t in ts}
    sites = set(site_of.get(osm_node(j)) for j in junctions) - {None}
    where = list(junctions | sites)
    position, names = {}, {}
    for chunk in range(0, len(where), 900):
        part = where[chunk:chunk+900]
        for node, lon, lat, name in fetch(con, f"SELECT node_id, longitude, latitude, name FROM nodes WHERE node_id IN ({','.join('?'*len(part))})", part):
            key = node if node in sites else osm_node(node)
            position[key] = (lon, lat); names[key] = name

    # Branches between (site-or-junction, voltage) buses.
    raw = []
    for node, kv, length, name, wkb, props in sections:
        ts = terminals.get(node, [])
        if len(ts) != 2 or wkb is None: continue
        prop = json.loads(props or '{}')
        if prop.get('frequency') == '0': continue
        v = level(kv)
        a, b = ((site_of.get(osm_node(t), osm_node(t)), v) for t in ts)
        if a == b: continue
        shape = shapely.from_wkb(wkb)
        coords = [list(c[:2]) for c in (shape.geoms[0] if shape.geom_type == 'MultiLineString' else shape).coords]
        digits = lambda key: int(str(prop.get(key, '0')).split(';')[0]) if str(prop.get(key, '0')).split(';')[0].isdigit() else 0
        cable = prop.get('power') == 'cable'
        circuits = max(1, digits('circuits'), digits('cables')//3, 2 if (not cable and v >= 132) else 1)
        raw.append({'a': a, 'b': b, 'kv': v, 'km': max(.05, length or 0.), 'kind': 'cable' if cable else 'line',
                    'circuits': circuits, 'name': name or prop.get('name'), 'coords': coords, 'osm': [node]})
    print(f'{len(raw):,} sections with two terminals', flush=True)

    # Loose ends: a route end (not in a site) within SNAP_KM of another bus of its voltage joins it.
    from scipy.spatial import cKDTree as _Tree
    degree = defaultdict(int)
    for e in raw: degree[e['a']] += 1; degree[e['b']] += 1
    keys = [k for k in degree if k[0] in position]
    snapped = {}
    for v in LEVELS:
        ks = [k for k in keys if k[1] == v]
        if len(ks) < 2: continue
        pts = np.array([position[k[0]] for k in ks]); kx = math.cos(math.radians(54))
        tree = _Tree(np.column_stack([pts[:, 0]*kx*111.3, pts[:, 1]*111.3]))
        for i, k in enumerate(ks):
            if degree[k] != 1 or k[0] in sites: continue
            dist, nbrs = tree.query(tree.data[i], k=4, distance_upper_bound=SNAP_KM)
            for d, j in zip(np.atleast_1d(dist), np.atleast_1d(nbrs)):
                if j != i and j < len(ks) and np.isfinite(d):
                    snapped[k] = ks[j]; break
    resolve = lambda k: snapped.get(k, k) if snapped.get(snapped.get(k, k)) is None else snapped.get(k, k)
    for e in raw: e['a'], e['b'] = resolve(e['a']), resolve(e['b'])
    raw = [e for e in raw if e['a'] != e['b']]
    print(f'{len(snapped):,} loose route ends snapped within {SNAP_KM} km', flush=True)

    # Contract series junctions (not sites) of one voltage and kind.
    for _ in range(50):
        adj = defaultdict(list)
        for i, e in enumerate(raw): adj[e['a']].append(i); adj[e['b']].append(i)
        dsu_dead, merged = set(), 0
        for u, inc in adj.items():
            if len(inc) != 2 or u[0] in sites or inc[0] in dsu_dead or inc[1] in dsu_dead: continue
            e1, e2 = raw[inc[0]], raw[inc[1]]
            if e1['kind'] != e2['kind'] or inc[0] == inc[1]: continue
            w1 = e1['b'] if e1['a'] == u else e1['a']; w2 = e2['b'] if e2['a'] == u else e2['a']
            if w1 == w2: continue
            c1 = e1['coords'] if e1['b'] == u else e1['coords'][::-1]
            c2 = e2['coords'] if e2['a'] == u else e2['coords'][::-1]
            raw[inc[0]] = {**e1, 'a': w1, 'b': w2, 'km': e1['km']+e2['km'], 'circuits': min(e1['circuits'], e2['circuits']),
                           'name': e1['name'] or e2['name'], 'coords': c1+c2[1:], 'osm': e1['osm']+e2['osm']}
            dsu_dead.add(inc[1]); merged += 1
        raw = [e for i, e in enumerate(raw) if i not in dsu_dead]
        if not merged: break

    # Buses and transformers.
    bus_keys = sorted({e['a'] for e in raw} | {e['b'] for e in raw}, key=str)
    by_site = defaultdict(set)
    for node, v in bus_keys: by_site[node].add(v)
    for node, vs in by_site.items():
        vs = sorted(vs)
        for low, high in zip(vs, vs[1:]):
            raw.append({'a': (node, low), 'b': (node, high), 'kv': high, 'kv_low': low, 'km': .1, 'kind': 'transformer',
                        'circuits': 2, 'name': names.get(node), 'coords': [position[node], position[node]], 'osm': []})
    # Separately mapped footprints of different voltage close together.
    single = [n for n, vs in by_site.items() if n in sites and n in position and len(vs) == 1]
    pts = np.array([position[n] for n in single]); kx = math.cos(math.radians(54))
    from scipy.spatial import cKDTree
    tree = cKDTree(np.column_stack([pts[:, 0]*kx*111.3, pts[:, 1]*111.3]))
    added = 0
    for i, j in tree.query_pairs(TRANSFORMER_KM):
        a, b = single[i], single[j]
        va, vb = by_site[a], by_site[b]
        if va & vb: continue
        (low,), (high,) = sorted([va, vb], key=min)
        la, hb = ((a, low), (b, high)) if low in va else ((b, low), (a, high))
        raw.append({'a': la, 'b': hb, 'kv': high, 'kv_low': low, 'km': .1, 'kind': 'transformer', 'circuits': 2,
                    'name': names.get(a) or names.get(b), 'coords': [position[a], position[b]], 'osm': []})
        added += 1
    print(f'{added} transformers between neighbouring footprints', flush=True)

    # Largest connected component.
    dsu = DSU(len(bus_keys)); index = {k: i for i, k in enumerate(bus_keys)}
    for e in raw: dsu.union(index[e['a']], index[e['b']])
    roots = defaultdict(int)
    for i in range(len(bus_keys)): roots[dsu.find(i)] += 1
    main = max(roots, key=roots.get)
    # Fragments (typically 33 kV networks whose feeding site's upper voltage isn't mapped nearby) join the
    # main network by an inferred transformer from their highest-voltage bus to the nearest
    # higher-voltage main bus within JOIN_FRAGMENT_KM.
    members = defaultdict(list)
    for k in bus_keys: members[dsu.find(index[k])].append(k)
    main_keys = [k for k in members[main] if k[0] in position]
    kx = math.cos(math.radians(54))
    to_xy = lambda keys: np.array([[position[k[0]][0]*kx*111.3, position[k[0]][1]*111.3] for k in keys])
    main_xy = to_xy(main_keys); main_kv = np.array([k[1] for k in main_keys])
    joined = 0
    for root, ks in members.items():
        if root == main: continue
        top = max((k for k in ks if k[0] in position), key=lambda k: k[1], default=None)
        if top is None: continue
        higher = np.flatnonzero(main_kv > top[1])
        if not len(higher): continue
        d = np.hypot(*(main_xy[higher]-to_xy([top])[0]).T)
        j = int(np.argmin(d))
        if d[j] > JOIN_FRAGMENT_KM: continue
        target = main_keys[higher[j]]
        raw.append({'a': top, 'b': target, 'kv': target[1], 'kv_low': top[1], 'km': .1, 'kind': 'transformer', 'circuits': 2,
                    'name': names.get(top[0]), 'coords': [position[top[0]], position[target[0]]], 'osm': [], 'inferred': True})
        dsu.union(index[top], index[target]); joined += 1
    main = dsu.find(index[main_keys[0]])
    print(f'{joined} fragments joined by inferred transformers', flush=True)
    keep = {k for k in bus_keys if dsu.find(index[k]) == main}
    total_km = sum(e['km'] for e in raw if e['kind'] != 'transformer')
    raw = [e for e in raw if e['a'] in keep]
    kept_km = sum(e['km'] for e in raw if e['kind'] != 'transformer')
    bus_keys = [k for k in bus_keys if k in keep]; index = {k: i for i, k in enumerate(bus_keys)}
    print(f'{len(bus_keys):,} buses, {len(raw):,} branches in the main component ({kept_km/total_km:.0%} of route km)', flush=True)

    # Terrain samples along overhead routes.
    profiles = defaultdict(list)
    osm_ids = sorted({o for e in raw for o in e['osm']})
    for chunk in range(0, len(osm_ids), 900):
        part = osm_ids[chunk:chunk+900]
        for node, distance, ele in fetch(con, f"SELECT node_id, distance_m, terrain_elevation_m FROM elevation_profiles WHERE node_id IN ({','.join('?'*len(part))}) ORDER BY node_id, part, distance_m", part):
            if ele is not None: profiles[node].append(ele)

    buses = []
    for (node, v) in bus_keys:
        lon, lat = position[node]
        buses.append({'key': f'{node}|{v}', 'lon': round(lon, 5), 'lat': round(lat, 5), 'kv': v,
                      'site': node in sites, 'name': names.get(node)})
    branches = []
    for e in raw:
        heights = [h for o in e['osm'] for h in profiles.get(o, [])]
        overhead = e['kind'] == 'line'
        n = max(2, round(e['km']/SAMPLE_KM)) if overhead else 0
        from scripts.build_gb_network import along
        points = along(e['coords'], [(j+.5)/n for j in range(n)]) if n else []
        if heights and n:
            idx = np.linspace(0, len(heights)-1, n).round().astype(int); ele = [float(heights[k]) for k in idx]
        else:
            ele = [0.]*n
        kv = e['kv']
        if e['kind'] == 'transformer':
            unit = TRANSFORMER_MW.get((e['kv_low'], kv), 90.)
            x = .16*BASE_MVA/unit/e['circuits']; rating = unit*e['circuits']
        else:
            zbase = kv**2/BASE_MVA
            x = ((CABLE_X_ALL if e['kind'] == 'cable' else X_OHM_KM_ALL)[kv])*e['km']/zbase/e['circuits']
            rating = (CABLE_RATING_ALL if e['kind'] == 'cable' else RATING_ALL)[kv]*e['circuits']
        branches.append({'from': index[e['a']], 'to': index[e['b']], 'kind': e['kind'], 'kv': kv, **({'inferred': True} if e.get('inferred') else {}),
                         **({'kv_low': e['kv_low']} if 'kv_low' in e else {}), 'length_km': round(e['km'], 3),
                         'circuits': e['circuits'], 'x_pu': x, 'rating_mw': rating, 'name': e['name'],
                         'pylons': max(1, round(e['km']*1000/SPAN_ALL[kv])) if overhead else 0,
                         'span_m': SPAN_ALL[kv] if overhead else 0, 'tower_height_m': HEIGHT_ALL[kv] if overhead else 0,
                         'fragility_median_kmh': FRAGILITY_MEDIAN[kv] if overhead else None,
                         'samples': [[round(p[0], 5), round(p[1], 5), round(h, 1)] for p, h in zip(points, ele)],
                         'geometry': [[round(c[0], 5), round(c[1], 5)] for c in simplify(e['coords'], .002)]})
    return {'buses': buses, 'branches': branches, 'kept_route_share': kept_km/total_km}


def attach_demand_and_generation(graph):
    """Demand from the backbone's demand map; generation from the asset index (with fuel)."""
    import pandas as pd
    from scipy.spatial import cKDTree
    kx = math.cos(math.radians(54))
    xy = lambda lon, lat: np.column_stack([np.asarray(lon)*kx, np.asarray(lat)])
    buses = graph['buses']
    pos = xy([b['lon'] for b in buses], [b['lat'] for b in buses])
    backbone = json.loads((ROOT/'data/gb_network.json').read_text())
    bb = xy([b['lon'] for b in backbone['buses']], [b['lat'] for b in backbone['buses']])
    supply = np.array([b['site'] and b['kv'] <= 132 for b in buses])
    owner = cKDTree(bb).query(pos)[1]
    load = np.zeros(len(buses))
    for k, b in enumerate(backbone['buses']):
        mine = np.flatnonzero((owner == k) & supply)
        if not len(mine): mine = [int(cKDTree(pos).query(bb[k])[1])]
        load[mine] += b['load_mw']/len(mine)
    for b, l in zip(buses, load): b['load_mw'] = round(float(l), 3)
    assets = pd.read_parquet(ROOT/'data/grid/asset_index.parquet', columns=['longitude', 'latitude', 'kind', 'mw', 'fuel', 'source'])
    gen = assets[assets.fuel.notna() & (assets.mw > 0)]
    # Official registers only: DUKES power stations, plus REPD renewables/storage not already in DUKES
    # (same fuel within 2 km). OSM and embedded-capacity registers re-map the same plants.
    dukes = gen[gen.source.str.startswith('DUKES')]
    repd = gen[gen.source.str.startswith('REPD')]
    near = cKDTree(xy(dukes.longitude, dukes.latitude)).query(xy(repd.longitude, repd.latitude), distance_upper_bound=2/111.3)
    hit = near[1] < len(dukes)
    same = np.zeros(len(repd), bool); same[hit] = dukes.fuel.to_numpy()[near[1][hit]] == repd.fuel.to_numpy()[hit]
    gen = pd.concat([dukes, repd[~same]])
    big = gen.mw.to_numpy() >= 100
    high = np.flatnonzero(np.array([b['kv'] for b in buses]) >= 132)
    target = cKDTree(pos).query(xy(gen.longitude, gen.latitude))[1]
    if big.any():
        target[big] = high[cKDTree(pos[high]).query(xy(gen.longitude[big], gen.latitude[big]))[1]]
    fuels = defaultdict(lambda: defaultdict(float))
    for t, mw, fuel in zip(target, gen.mw.to_numpy(), gen.fuel.to_numpy()):
        fuels[int(t)][fuel] += float(mw)
    for imp in backbone.get('interconnectors', []):
        b = backbone['buses'][imp['bus']-1]
        t = int(high[cKDTree(pos[high]).query(xy([b['lon']], [b['lat']]))[1][0]])
        fuels[t]['interconnector'] += imp['mw']
    generators = []
    total_base = 0.
    for t in sorted(fuels):
        cap = sum(fuels[t].values()); base = sum(mw*CAPACITY_FACTOR.get(f, .3) for f, mw in fuels[t].items())
        generators.append({'bus': t, 'max_mw': round(cap, 2), 'base_mw': base, 'fuels': {f: round(m, 2) for f, m in fuels[t].items()}})
        total_base += base
    demand = load.sum()
    for g in generators: g['base_mw'] = round(min(g['max_mw'], g['base_mw']*demand/total_base), 3)
    graph['generators'] = generators
    return graph


def dc_flows(n, branches, injection):
    """Sparse lossless DC flows for one connected network (reference bus 0)."""
    from scipy import sparse
    from scipy.sparse.linalg import spsolve
    f = np.array([e['from'] for e in branches]); t = np.array([e['to'] for e in branches])
    b = np.array([BASE_MVA/e['x_pu'] for e in branches])
    B = sparse.coo_matrix((np.concatenate([b, b, -b, -b]), (np.concatenate([f, t, f, t]), np.concatenate([f, t, t, f]))), shape=(n, n)).tocsc()
    theta = np.zeros(n); theta[1:] = spsolve(B[1:, 1:], injection[1:])
    return b*(theta[f]-theta[t])


def size_ratings(graph, margin=.85):
    """Distribution ratings are not published here, so size them as networks are planned: raise any
    branch above `margin` of its rating at the winter peak/trough, calm or windy (never lower)."""
    from backend.grid_ops import FIRM_AVAILABILITY, WIND, demand_factor, wind_power_fraction
    n = len(graph['buses']); load = np.array([b['load_mw'] for b in graph['buses']])
    wind, firm = np.zeros(n), np.zeros(n)
    for g in graph['generators']:
        for f, mw in g['fuels'].items():
            if f in WIND: wind[g['bus']] += mw
            elif f != 'solar': firm[g['bus']] += mw*FIRM_AVAILABILITY.get(f, .5)
    peak = np.zeros(len(graph['branches']))
    for stamp, gust in [('2026-01-01T18:00:00Z', 0.), ('2026-01-01T18:00:00Z', 60.), ('2026-01-01T04:00:00Z', 0.), ('2026-01-01T04:00:00Z', 60.)]:
        demand = load*demand_factor([stamp], load.sum())[0]
        w = wind*wind_power_fraction(gust); used = min(w.sum(), demand.sum())
        generation = w*used/max(w.sum(), 1e-9) + firm*(demand.sum()-used)/firm.sum()
        peak = np.maximum(peak, np.abs(dc_flows(n, graph['branches'], generation-demand)))
    raised = 0
    for e, flow in zip(graph['branches'], peak):
        if flow > margin*e['rating_mw']:
            unit = e['rating_mw']/e['circuits']
            e['rating_mw'] = round(flow/margin, 1); e['rating_sized'] = True; raised += 1
    graph['ratings_raised'] = raised
    return raised


if __name__ == '__main__':
    graph = attach_demand_and_generation(build())
    print(f'{size_ratings(graph):,} branch ratings raised to carry the sizing cases', flush=True)
    destination = ROOT/'data/grid/detail_graph.json'
    destination.write_text(json.dumps(graph, separators=(',', ':')))
    cap = sum(g['max_mw'] for g in graph['generators'])
    kinds = defaultdict(int)
    for e in graph['branches']: kinds[e['kind']] += 1
    print(f"{len(graph['buses']):,} buses, {len(graph['branches']):,} branches {dict(kinds)}, {cap:,.0f} MW generation, "
          f"{sum(b['load_mw'] for b in graph['buses']):,.0f} MW demand -> {destination.relative_to(ROOT)} "
          f"({destination.stat().st_size/1e6:.0f} MB)")
