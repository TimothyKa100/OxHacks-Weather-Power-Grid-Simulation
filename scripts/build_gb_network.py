"""Build data/gb_network.json from jacobwright32/uk-grid-atlas (MIT; data OSM ODbL).

Usage: python scripts/build_gb_network.py /path/to/uk-grid-atlas

OSM transmission ways become a bus/branch graph: way endpoints are clustered
into substation buses, series chains are contracted, and the largest connected
component is kept. Reactances, ratings, dispatch and demand allocation are
illustrative assumptions, not National Grid ESO data.
"""
import json
import math
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAP_KM = 2.0            # endpoints this close are treated as one substation
GEN_SNAP_KM = 40.0       # stations further than this from any bus are ignored
TOTAL_DEMAND_MW = 30000  # illustrative GB transmission demand (typical winter evening ~35-45 GW)
BASE_MVA = 100.
# Illustrative per-circuit series reactance (ohm/km) and thermal rating (MW).
X_OHM_KM = {400: .30, 275: .35, 132: .40}
RATING_MW = {400: 2200., 275: 900., 132: 180.}
# Typical span between lattice towers (m). GB 400/275 kV L-series towers run ~300-400 m
# apart; 132 kV towers are smaller and closer. Pylon count = route length / span.
SPAN_M = {400: 360., 275: 350., 132: 280.}
# Typical tower height (m) for the Eurocode altitude factor's height adjustment.
TOWER_HEIGHT_M = {400: 50., 275: 46., 132: 27.}
SAMPLE_KM = 2.0          # elevation/wind sample spacing along each route
ELEVATION_API = 'https://api.opentopodata.org/v1/eudem25m'  # EU-DEM v1.1 25 m; public: 100 pts/req, 1 req/s
ELEVATION_CACHE = ROOT/'data/gb_elevation_cache.json'
# Illustrative dispatch share of installed capacity at the reference hour.
CAPACITY_FACTOR = {'nuclear': .85, 'gas': .55, 'wind_offshore': .40, 'wind_onshore': .30, 'hydro': .35,
                   'pumped': .20, 'bioenergy': .70, 'waste': .70, 'storage': .10, 'oil': .05,
                   'solar': .05, 'marine': .30, 'other': .30, 'coal': .0}
# Major GB urban areas (approximate built-up-area population, thousands) to place demand.
CITIES = [
    ('London', -0.12, 51.51, 9700), ('Birmingham', -1.90, 52.48, 2600), ('Manchester', -2.24, 53.48, 2700),
    ('Leeds', -1.55, 53.80, 1800), ('Glasgow', -4.25, 55.86, 1000), ('Liverpool', -2.98, 53.41, 900),
    ('Newcastle', -1.61, 54.98, 800), ('Sheffield', -1.47, 53.38, 700), ('Bristol', -2.59, 51.45, 700),
    ('Nottingham', -1.15, 52.95, 730), ('Leicester', -1.13, 52.64, 510), ('Edinburgh', -3.19, 55.95, 530),
    ('Cardiff', -3.18, 51.48, 480), ('Southampton', -1.40, 50.91, 860), ('Brighton', -0.14, 50.82, 470),
    ('Hull', -0.33, 53.74, 320), ('Stoke', -2.18, 53.00, 370), ('Coventry', -1.51, 52.41, 360),
    ('Plymouth', -4.14, 50.38, 260), ('Reading', -0.97, 51.45, 340), ('Teesside', -1.23, 54.57, 380),
    ('Swansea', -3.94, 51.62, 300), ('Aberdeen', -2.10, 57.15, 220), ('Dundee', -2.97, 56.46, 150),
    ('Norwich', 1.30, 52.63, 210), ('Exeter', -3.53, 50.72, 130), ('Cambridge', 0.12, 52.21, 160),
    ('Oxford', -1.26, 51.75, 170), ('Milton Keynes', -0.76, 52.04, 260), ('Peterborough', -0.24, 52.57, 200),
    ('Ipswich', 1.16, 52.06, 180), ('Kent Medway', 0.52, 51.38, 330), ('Preston', -2.70, 53.76, 330),
    ('York', -1.08, 53.96, 210), ('Derby', -1.48, 52.92, 270), ('Inverness', -4.22, 57.48, 60),
    ('Carlisle', -2.94, 54.89, 75), ('Lincoln', -0.54, 53.23, 120), ('Bournemouth', -1.88, 50.72, 470),
    ('Swindon', -1.78, 51.56, 220), ('Gloucester', -2.24, 51.86, 300), ('Northampton', -0.90, 52.24, 220),
]


def km(a, b):
    lat = math.radians((a[1]+b[1])/2)
    return 6371*math.hypot(math.radians(b[0]-a[0])*math.cos(lat), math.radians(b[1]-a[1]))


def line_km(coords):
    return sum(km(a, b) for a, b in zip(coords, coords[1:]))


def elevations(points):
    """Terrain height (m) for (lon, lat) points via OpenTopoData, cached on disk by rounded position."""
    cache = json.loads(ELEVATION_CACHE.read_text()) if ELEVATION_CACHE.exists() else {}
    keys = [f'{lat:.4f},{lon:.4f}' for lon, lat in points]
    missing = sorted(set(k for k in keys if k not in cache))
    for i in range(0, len(missing), 100):
        batch = missing[i:i+100]
        query = f"{ELEVATION_API}?locations={'|'.join(batch)}"
        for attempt in range(8):
            try:
                with urllib.request.urlopen(query, timeout=60) as r: values = [x['elevation'] for x in json.load(r)['results']]
                break
            except OSError as exc:
                if attempt == 7: raise
                # Free tier is rate limited; honour Retry-After, else back off up to a minute.
                wait = int(getattr(exc, 'headers', None) and exc.headers.get('Retry-After') or min(60, 5*2**attempt))
                print(f'  elevation API: {exc}; retrying in {wait}s', file=sys.stderr); time.sleep(wait)
        cache.update(zip(batch, values))
        ELEVATION_CACHE.write_text(json.dumps(cache, sort_keys=True, separators=(',', ':')))  # resumable
        print(f'  elevation {min(i+100, len(missing))}/{len(missing)}', file=sys.stderr)
        time.sleep(1.2)
    return [max(0., float(cache[k] or 0.)) for k in keys]  # sea (no data) / below datum counts as sea level


def along(coords, fractions):
    """Points at the given fractions of a polyline's length."""
    seg = [km(a, b) for a, b in zip(coords, coords[1:])]; total = sum(seg) or 1.
    out, i, done = [], 0, 0.
    for f in fractions:
        target = f*total
        while i < len(seg)-1 and done+seg[i] < target: done += seg[i]; i += 1
        t = (target-done)/seg[i] if seg[i] else 0.
        a, b = coords[i], coords[i+1]
        out.append((a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t))
    return out


def simplify(coords, tolerance=.003):
    """Ramer-Douglas-Peucker in degrees; keeps endpoints."""
    if len(coords) < 3: return coords
    keep, stack = {0, len(coords)-1}, [(0, len(coords)-1)]
    while stack:
        i, j = stack.pop(); (ax, ay), (bx, by) = coords[i], coords[j]
        dx, dy = bx-ax, by-ay; norm = math.hypot(dx, dy) or 1e-12
        best, k = 0., None
        for m in range(i+1, j):
            d = abs(dy*(coords[m][0]-ax)-dx*(coords[m][1]-ay))/norm
            if d > best: best, k = d, m
        if k is not None and best > tolerance: keep.add(k); stack += [(i, k), (k, j)]
    return [coords[i] for i in sorted(keep)]


class DSU:
    def __init__(self, n): self.p = list(range(n))
    def find(self, a):
        while self.p[a] != a: self.p[a] = self.p[self.p[a]]; a = self.p[a]
        return a
    def union(self, a, b): self.p[self.find(a)] = self.find(b)


def build(atlas):
    lines = json.loads((atlas/'src/data/gb/transmission.json').read_text())['features']
    stations = json.loads((atlas/'src/data/gb/stations.json').read_text())['features']
    meta = json.loads((atlas/'src/data/gb/meta.json').read_text())

    # 1 / Cluster way endpoints into substations (grid hash + union-find).
    points = []
    for f in lines:
        c = f['geometry']['coordinates']; points += [c[0], c[-1]]
    dsu = DSU(len(points)); cell = SNAP_KM/111.; grid = defaultdict(list)
    for i, p in enumerate(points): grid[(int(p[0]/cell*.6), int(p[1]/cell))].append(i)
    for (gx, gy), members in grid.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx+dx, gy+dy), ()):
                    for i in members:
                        if i < j and km(points[i], points[j]) <= SNAP_KM: dsu.union(i, j)
    root_to_node, node_pts = {}, defaultdict(list)
    for i, p in enumerate(points):
        node_pts[root_to_node.setdefault(dsu.find(i), len(root_to_node))].append(p)
    pos = {k: (sum(p[0] for p in v)/len(v), sum(p[1] for p in v)/len(v)) for k, v in node_pts.items()}

    # 2 / Edges: one per OSM way; parallel ways between the same pair become extra circuits.
    edges = {}
    for k, f in enumerate(lines):
        a, b = root_to_node[dsu.find(2*k)], root_to_node[dsu.find(2*k+1)]
        if a == b: continue
        v = f['properties']['v']; length = max(.5, line_km(f['geometry']['coordinates']))
        key = (min(a, b), max(a, b), v)
        coords = f['geometry']['coordinates'] if a < b else f['geometry']['coordinates'][::-1]  # oriented a -> b
        e = edges.setdefault(key, {'a': key[0], 'b': key[1], 'kv': v, 'km': length, 'circuits': 0,
                                   'names': set(), 'coords': coords})
        e['circuits'] += 1
        if length < e['km']: e['km'], e['coords'] = length, coords
        if f['properties'].get('name'): e['names'].add(f['properties']['name'])
    for e in edges.values():
        zbase = e['kv']**2/BASE_MVA
        e['x_pu'] = X_OHM_KM[e['kv']]*e['km']/zbase/e['circuits']
        e['rating'] = RATING_MW[e['kv']]*e['circuits']
    edges = list(edges.values())

    # 3 / Largest connected component.
    adj = defaultdict(set)
    for i, e in enumerate(edges): adj[e['a']].add(i); adj[e['b']].add(i)
    seen, best = set(), []
    for start in adj:
        if start in seen: continue
        comp, stack = [], [start]; seen.add(start)
        while stack:
            u = stack.pop(); comp.append(u)
            for i in adj[u]:
                w = edges[i]['b'] if edges[i]['a'] == u else edges[i]['a']
                if w not in seen: seen.add(w); stack.append(w)
        if len(comp) > len(best): best = comp
    keep = set(best)
    edges = [e for e in edges if e['a'] in keep]

    # 4 / Contract series chains (degree-2 nodes, same voltage) into single branches.
    changed = True
    while changed:
        changed = False
        adj = defaultdict(list)
        for i, e in enumerate(edges): adj[e['a']].append(i); adj[e['b']].append(i)
        dead = set()
        for u, inc in adj.items():
            if len(inc) != 2 or inc[0] in dead or inc[1] in dead: continue
            e1, e2 = edges[inc[0]], edges[inc[1]]
            if e1['kv'] != e2['kv']: continue
            w1 = e1['b'] if e1['a'] == u else e1['a']; w2 = e2['b'] if e2['a'] == u else e2['a']
            if w1 == w2: continue
            if any({x['a'], x['b']} == {w1, w2} and x['kv'] == e1['kv'] for x in edges): continue
            c1 = e1['coords'] if e1['a'] == w1 else e1['coords'][::-1]   # w1 -> u
            c2 = e2['coords'] if e2['a'] == u else e2['coords'][::-1]    # u -> w2
            path = c1+c2[1:]
            merged = {'a': min(w1, w2), 'b': max(w1, w2), 'kv': e1['kv'], 'km': e1['km']+e2['km'],
                      'circuits': min(e1['circuits'], e2['circuits']), 'names': e1['names'] | e2['names'],
                      'x_pu': e1['x_pu']+e2['x_pu'], 'rating': min(e1['rating'], e2['rating']),
                      'coords': path if w1 < w2 else path[::-1]}
            edges[inc[0]] = merged; dead.add(inc[1]); changed = True
            break  # adjacency is stale after a merge
        edges = [e for i, e in enumerate(edges) if i not in dead]

    used = sorted({e['a'] for e in edges} | {e['b'] for e in edges})
    index = {u: i for i, u in enumerate(used)}
    lon = [pos[u][0] for u in used]; lat = [pos[u][1] for u in used]
    kv = defaultdict(int)
    for e in edges:
        for u in (e['a'], e['b']): kv[index[u]] = max(kv[index[u]], e['kv'])

    # 5 / Generators: snap stations with known capacity to the nearest bus.
    gen = defaultdict(lambda: defaultdict(float)); skipped = 0
    for s in stations:
        mw = s['properties'].get('capacityMW')
        if not mw or mw < 1: continue
        p = s['geometry']['coordinates']
        d, i = min((km(p, (lon[i], lat[i])), i) for i in range(len(used)))
        if d > GEN_SNAP_KM: skipped += 1; continue
        gen[i][s['properties']['fuel']] += mw

    # 6 / Demand: each city split across its three nearest buses (inverse distance), plus a
    # small uniform base load at every substation.
    weight = [0.]*len(used)
    for _, x, y, pop in CITIES:
        near = sorted((km((x, y), (lon[i], lat[i])), i) for i in range(len(used)))[:3]
        inv = [1/(d+3.) for d, _ in near]
        for (d, i), w in zip(near, inv): weight[i] += pop*w/sum(inv)
    total_w = sum(weight)
    share = [.85*w/total_w + .15/len(used) for w in weight]
    load = [TOTAL_DEMAND_MW*s for s in share]

    generators, base_total = [], 0.
    for i in sorted(gen):
        cap = sum(gen[i].values()); base = sum(mw*CAPACITY_FACTOR.get(f, .3) for f, mw in gen[i].items())
        generators.append({'bus': i+1, 'max_mw': cap, 'base_mw': base,
                           'fuels': {f: round(mw, 1) for f, mw in sorted(gen[i].items(), key=lambda x: -x[1])}})
        base_total += base
    # Scale reference dispatch so it balances demand (still within capacity).
    for g in generators: g['base_mw'] = min(g['max_mw'], g['base_mw']*TOTAL_DEMAND_MW/base_total)

    x0, x1, y0, y1 = min(lon), max(lon), min(lat), max(lat)
    # Equirectangular projection at GB latitude, normalised to the north-south extent.
    project = lambda p: [round((p[0]-x0)*math.cos(math.radians(54))/(y1-y0), 5), round((y1-p[1])/(y1-y0), 5)]

    # 7 / Elevation of every substation and of sample points every ~SAMPLE_KM along each route.
    edges = sorted(edges, key=lambda e: (-e['kv'], index[e['a']], index[e['b']]))
    samples = []
    for e in edges:
        n = max(2, round(e['km']/SAMPLE_KM))
        samples.append(along(e['coords'], [(j+.5)/n for j in range(n)]))
    flat = [p for group in samples for p in group]
    heights = elevations([(lon[i], lat[i]) for i in range(len(used))]+flat)
    bus_ele, sample_ele = heights[:len(used)], heights[len(used):]

    buses = [{'id': i+1, 'load_mw': round(load[i], 3), 'lon': round(lon[i], 5), 'lat': round(lat[i], 5),
              'x': project((lon[i], lat[i]))[0], 'y': project((lon[i], lat[i]))[1], 'ele_m': round(bus_ele[i], 1),
              'kv': kv[i]} for i in range(len(used))]
    branches, cursor = [], 0
    for k, e in enumerate(edges):
        names = sorted(e['names'])
        group = samples[k]; ele = sample_ele[cursor:cursor+len(group)]; cursor += len(group)
        pylons = max(1, round(e['km']*1000/SPAN_M[e['kv']]))
        branches.append({'id': f'L{k+1:03d}', 'from': index[e['a']]+1, 'to': index[e['b']]+1, 'x_pu': e['x_pu'], 'tap': 1.,
                         'kv': e['kv'], 'length_km': round(e['km'], 2),
                         'pylons': pylons, 'span_m': SPAN_M[e['kv']], 'tower_height_m': TOWER_HEIGHT_M[e['kv']],
                         'ele_max_m': round(max(ele), 1), 'ele_mean_m': round(sum(ele)/len(ele), 1),
                         # Route points (x, y, terrain m); each carries an equal share of the line's pylons.
                         'samples': [project(p)+[round(h, 1)] for p, h in zip(group, ele)],
                         'geometry': [project(p) for p in simplify(e['coords'])], 'circuits': e['circuits'], 'rating_mw': e['rating'],
                         'name': names[0] if names else None,
                         # Longer routes cost more to harden and are more exposed (illustrative).
                         'cost': 1 if e['km'] < 25 else 2 if e['km'] < 60 else 3,
                         'exposure': round(min(.8, .05+.004*e['km']), 4)})
    return {'source': 'jacobwright32/uk-grid-atlas src/data/gb (OpenStreetMap power=line, 132-400 kV)',
            'elevation_source': 'EU-DEM v1.1 (25 m, Copernicus Land Monitoring Service) via OpenTopoData',
            'attribution': meta.get('attribution'), 'atlas_generated': meta.get('generated'),
            'base_mva': BASE_MVA, 'geographic': True, 'buses': buses, 'generators': generators, 'branches': branches,
            'notes': (f'{len(lines)} OSM ways clustered at {SNAP_KM} km into substations; series chains contracted; '
                      f'largest component kept. {skipped} stations beyond {GEN_SNAP_KM} km skipped. '
                      'Reactances, thermal ratings, dispatch, demand allocation, costs and exposure are illustrative.')}


if __name__ == '__main__':
    out = build(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT.parent/'uk-grid-atlas'))
    (ROOT/'data/gb_network.json').write_text(json.dumps(out, separators=(',', ':')))
    cap = sum(g['max_mw'] for g in out['generators'])
    print(f"{len(out['buses'])} buses, {len(out['branches'])} branches, {len(out['generators'])} generator buses, "
          f"{cap:.0f} MW capacity, {sum(b['load_mw'] for b in out['buses']):.0f} MW demand")
