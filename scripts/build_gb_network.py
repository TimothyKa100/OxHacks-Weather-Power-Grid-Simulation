"""Build data/gb_network.json from jacobwright32/uk-grid-atlas (MIT; data OSM ODbL).

Usage: python scripts/build_gb_network.py /path/to/uk-grid-atlas [/path/to/uk_power_grid.sqlite]

The optional database (the dataRefininf grid archive, default data/grid/uk_power_grid.sqlite)
supplies OSM underground cables at 275/400 kV, which the atlas omits; London's supply
depends on them.

OSM transmission ways become a bus/branch graph: way endpoints of the same voltage
are clustered into substation buses (one bus per voltage level at a site), ways are split where another route ends on them (missing
T-joins), separate routes stay separate branches, nearby substations of different
voltage are joined by transformers sized to the local demand, dead ends are linked to a nearby substation
of the same voltage where one exists (unmapped cable or continuation), series
chains are contracted and the largest connected component is kept. Overhead
routes are assumed to carry two circuits on one set of towers. Remaining dead
ends at the edge of coverage get a boundary supply standing in for the
unmodelled network. Operational interconnectors are imports at their landing
substation. Reactances, ratings, dispatch and demand allocation are
illustrative assumptions, not NESO data. scripts/security_analysis.py checks
the result against single-circuit outages.
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
GEN_SNAP_KM = 20.0       # stations further than this from any bus are ignored (embedded, not transmission)
JOIN_KM = 1.0            # a route ending this close to another route's interior joins it there
SAME_TOWER_KM = 0.25     # parallel ways this close along their length share towers (extra circuits)
TOWER_CIRCUITS = 2       # GB overhead routes are almost all double-circuit towers (assumption)
TRANSFORMER_KM = 4.0     # substations of different voltage this close are joined by transformers
GAP_KM = 10.0            # a dead end links to a same-voltage substation this close (unmapped cable/route)
CITY_SHARE = .55         # share of demand placed at listed cities (their share of GB population, with industry)
BOUNDARY_SHARE = .5      # boundary supply at remaining dead ends, as a share of their demand
# Underground cable: lower reactance, lower rating per circuit than overhead line (illustrative).
CABLE_X_OHM_KM = {400: .12, 275: .13}
CABLE_RATING_MW = {400: 1600., 275: 900.}
# Two supergrid transformers per site: (rating MW per unit, reactance pu on own base).
TRANSFORMER = {(275, 400): (1000., .16), (132, 400): (240., .16), (132, 275): (240., .16)}
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


def point_segment_km(p, a, b):
    """Distance (km) from p to segment ab, and the fraction t along it."""
    lat = math.radians(p[1]); kx = 111.32*math.cos(lat); ky = 110.57
    ax, ay, bx, by, px, py = a[0]*kx, a[1]*ky, b[0]*kx, b[1]*ky, p[0]*kx, p[1]*ky
    dx, dy = bx-ax, by-ay; length2 = dx*dx+dy*dy
    t = 0. if not length2 else max(0., min(1., ((px-ax)*dx+(py-ay)*dy)/length2))
    return math.hypot(px-(ax+t*dx), py-(ay+t*dy)), t


def mean_offset_km(coords, other, samples=8):
    """Mean distance from points along one route to the other route."""
    points = along(coords, [(j+.5)/samples for j in range(samples)])
    return sum(min(point_segment_km(p, a, b)[0] for a, b in zip(other, other[1:])) for p in points)/samples


def load_cables(database):
    """AC underground transmission cables (275/400 kV) from the archive's OSM layer, as atlas-like features.

    132 kV cables are left out: London's 132 kV networks run split into groups from separate
    supply points (normally-open points), so modelling them as a closed mesh would invent
    parallel paths beside the supergrid."""
    import sqlite3
    import shapely
    if not database or not Path(database).exists(): return []
    con = sqlite3.connect(f'file:{database}?mode=ro', uri=True)
    rows = con.execute("""SELECT name, voltage_kv, geometry_wgs84_wkb, properties_json FROM nodes
                          WHERE source LIKE 'OSM%' AND node_type = 'power_line' AND voltage_kv IN (275, 400)
                          AND (properties_json LIKE '%"power": "cable"%' OR properties_json LIKE '%"power":"cable"%')""").fetchall()
    features = []
    for name, v, geometry, properties in rows:
        prop = json.loads(properties or '{}')
        if prop.get('frequency') == '0' or geometry is None: continue  # HVDC
        shape = shapely.from_wkb(geometry)
        parts = shape.geoms if shape.geom_type == 'MultiLineString' else [shape]
        number = lambda key: int(str(prop.get(key, '0')).split(';')[0]) if str(prop.get(key, '0')).split(';')[0].isdigit() else 0
        circuits = max(1, number('circuits'), number('cables')//3)  # three single-core cables per circuit
        for part in parts:
            coords = [list(c[:2]) for c in part.coords]
            if len(coords) >= 2:
                features.append({'geometry': {'type': 'LineString', 'coordinates': coords},
                                 'properties': {'v': int(v), 'name': name or prop.get('name'), 'kind': 'cable', 'circuits': circuits}})
    return features


def cluster_endpoints(lines):
    """Substation buses: endpoints within SNAP_KM *of the same voltage*. Voltage levels at one
    site stay separate buses, joined later by transformers (their impedance limits parallel flow)."""
    points, volts = [], []
    for f in lines:
        c = f['geometry']['coordinates']; points += [c[0], c[-1]]; volts += [f['properties']['v']]*2
    dsu = DSU(len(points)); cell = SNAP_KM/111.; grid = defaultdict(list)
    for i, p in enumerate(points): grid[(int(p[0]/cell*.6), int(p[1]/cell))].append(i)
    for (gx, gy), members in grid.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx+dx, gy+dy), ()):
                    for i in members:
                        if i < j and volts[i] == volts[j] and km(points[i], points[j]) <= SNAP_KM: dsu.union(i, j)
    root_to_node, node_pts = {}, defaultdict(list)
    for i, p in enumerate(points):
        node_pts[root_to_node.setdefault(dsu.find(i), len(root_to_node))].append(p)
    pos = {k: (sum(p[0] for p in v)/len(v), sum(p[1] for p in v)/len(v)) for k, v in node_pts.items()}
    ends = [(root_to_node[dsu.find(2*k)], root_to_node[dsu.find(2*k+1)]) for k in range(len(lines))]
    node_kv = {root_to_node[dsu.find(i)]: v for i, v in enumerate(volts)}
    return pos, ends, node_kv


def split_at_joins(lines, pos, ends, node_kv):
    """Pieces (coords, node a, node b, kv, name): routes are cut where another route's
    substation lies within JOIN_KM of their interior (a T-join the clustering missed)."""
    cell = .05; grid = defaultdict(list)
    for k, f in enumerate(lines):
        c = f['geometry']['coordinates']
        for s, (a, b) in enumerate(zip(c, c[1:])):
            for gx in range(int(min(a[0], b[0])/cell)-1, int(max(a[0], b[0])/cell)+2):
                for gy in range(int(min(a[1], b[1])/cell)-1, int(max(a[1], b[1])/cell)+2):
                    grid[(gx, gy)].append((k, s))
    cuts = defaultdict(dict)  # way -> node -> (segment, t, distance)
    for node, p in pos.items():
        for k, s in set(grid.get((int(p[0]/cell), int(p[1]/cell)), ())):
            if node in ends[k] or node_kv[node] != lines[k]['properties']['v']: continue
            c = lines[k]['geometry']['coordinates']
            d, t = point_segment_km(p, c[s], c[s+1])
            if d > JOIN_KM or (s == 0 and t < 1e-6) or (s == len(c)-2 and t > 1-1e-6): continue
            if node not in cuts[k] or d < cuts[k][node][2]: cuts[k][node] = (s, t, d)
    pieces, joins = [], 0
    for k, f in enumerate(lines):
        c = f['geometry']['coordinates']; v = f['properties']['v']; name = f['properties'].get('name')
        kind = f['properties'].get('kind', 'line'); circuits = f['properties'].get('circuits')
        stops = sorted((s+t, node) for node, (s, t, _) in cuts.get(k, {}).items())
        start_node, start_coords, prev = ends[k][0], [c[0]], 0.
        vertex = 1
        for at, node in stops:
            s, t = int(at), at-int(at)
            while vertex <= s: start_coords.append(c[vertex]); vertex += 1
            a, b = c[s], c[s+1]
            split = (a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t)
            pieces.append((start_coords+[split], start_node, node, v, name, kind, circuits)); joins += 1
            start_node, start_coords = node, [split]
        pieces.append((start_coords+c[vertex:], start_node, ends[k][1], v, name, kind, circuits))
    return pieces, joins


def build(atlas, database=None):
    overhead_ways = json.loads((atlas/'src/data/gb/transmission.json').read_text())['features']
    cables = load_cables(database)
    lines = overhead_ways+cables
    stations = json.loads((atlas/'src/data/gb/stations.json').read_text())['features']
    links = json.loads((atlas/'src/data/gb/interconnectors.json').read_text())['features']
    meta = json.loads((atlas/'src/data/gb/meta.json').read_text())

    # 1 / Cluster way endpoints into substations; cut routes at missing T-joins.
    pos, ends, node_kv = cluster_endpoints(lines)
    pieces, joins = split_at_joins(lines, pos, ends, node_kv)

    # 2 / Edges per route. Ways between the same substations share towers only if they run
    # together; separate routes stay separate branches (one collapse doesn't take both).
    routes = defaultdict(list)
    for coords, a, b, v, name, kind, circuits in pieces:
        if a == b: continue
        length = max(.5 if kind == 'line' else .1, line_km(coords))
        coords = coords if a < b else coords[::-1]
        key = (min(a, b), max(a, b), v, kind)
        for e in routes[key]:
            if mean_offset_km(coords, e['coords']) <= SAME_TOWER_KM:
                e['ways'] += 1; e['tagged'] += circuits or 1
                if name: e['names'].add(name)
                break
        else:
            routes[key].append({'a': key[0], 'b': key[1], 'kv': v, 'km': length, 'ways': 1, 'kind': kind, 'tagged': circuits or 1,
                                'names': {name} if name else set(), 'coords': coords})
    edges = [e for group in routes.values() for e in group]
    for e in edges:
        # Towers carry two circuits; cables carry what OSM records (one per mapped cable route by default).
        e['circuits'] = max(TOWER_CIRCUITS, e['ways']) if e['kind'] == 'line' else max(1, e['tagged'])

    # 3 / Transformers between nearby substations of different voltage, then links from dead
    # ends to the nearest same-voltage substation within reach.
    volts = defaultdict(set)
    for e in edges: volts[e['a']].add(e['kv']); volts[e['b']].add(e['kv'])
    adjacent = {(e['a'], e['b']) for e in edges}
    nodes = sorted(volts)
    transformers = gaps = 0
    for i, u in enumerate(nodes):
        for w in nodes[i+1:]:
            if abs(pos[u][1]-pos[w][1]) > TRANSFORMER_KM/100 or (u, w) in adjacent or volts[u] & volts[w]: continue
            d = km(pos[u], pos[w])
            if d > TRANSFORMER_KM: continue
            low, high = min(volts[u] | volts[w]), max(volts[u] | volts[w])
            if (low, high) not in TRANSFORMER: continue
            edges.append({'a': u, 'b': w, 'kv': high, 'kv_low': low, 'km': d, 'ways': 1, 'kind': 'transformer',
                          'names': set(), 'coords': [pos[u], pos[w]], 'circuits': 2})
            adjacent.add((u, w)); transformers += 1
    degree = defaultdict(int)
    for e in edges: degree[e['a']] += 1; degree[e['b']] += 1
    for u in nodes:
        if degree[u] != 1: continue
        options = sorted((km(pos[u], pos[w]), w) for w in nodes
                         if w != u and volts[u] & volts[w] and (min(u, w), max(u, w)) not in adjacent)
        if options and options[0][0] <= GAP_KM:
            d, w = options[0]
            a, b = min(u, w), max(u, w)
            edges.append({'a': a, 'b': b, 'kv': max(volts[u] & volts[w]), 'km': max(.5, d), 'ways': 1, 'kind': 'link',
                          'names': set(), 'coords': [pos[a], pos[b]], 'circuits': 2})
            adjacent.add((a, b)); degree[u] += 1; degree[w] += 1; gaps += 1

    # 4 / Largest connected component.
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
    route_km_total = sum(e['km'] for e in edges if e['kind'] == 'line')
    edges = [e for e in edges if e['a'] in keep]
    # Cable spurs that lead nowhere (e.g. offshore export cables) are not part of the backbone.
    while True:
        degree = defaultdict(int)
        for e in edges: degree[e['a']] += 1; degree[e['b']] += 1
        spurs = {i for i, e in enumerate(edges) if e['kind'] == 'cable' and (degree[e['a']] == 1 or degree[e['b']] == 1)}
        if not spurs: break
        edges = [e for i, e in enumerate(edges) if i not in spurs]
    kept_share = sum(e['km'] for e in edges if e['kind'] == 'line')/route_km_total

    # 5 / Contract series chains of overhead line (same voltage, degree 2) into single branches.
    changed = True
    while changed:
        changed = False
        adj = defaultdict(list)
        for i, e in enumerate(edges): adj[e['a']].append(i); adj[e['b']].append(i)
        dead = set()
        for u, inc in adj.items():
            if len(inc) != 2 or inc[0] in dead or inc[1] in dead: continue
            e1, e2 = edges[inc[0]], edges[inc[1]]
            if e1['kv'] != e2['kv'] or e1['kind'] != e2['kind'] or e1['kind'] not in ('line', 'cable'): continue
            w1 = e1['b'] if e1['a'] == u else e1['a']; w2 = e2['b'] if e2['a'] == u else e2['a']
            if w1 == w2: continue
            if any({x['a'], x['b']} == {w1, w2} and x['kv'] == e1['kv'] and x['kind'] == e1['kind'] for x in edges): continue
            c1 = e1['coords'] if e1['a'] == w1 else e1['coords'][::-1]   # w1 -> u
            c2 = e2['coords'] if e2['a'] == u else e2['coords'][::-1]    # u -> w2
            path = c1+c2[1:]
            merged = {'a': min(w1, w2), 'b': max(w1, w2), 'kv': e1['kv'], 'km': e1['km']+e2['km'], 'kind': e1['kind'],
                      'ways': min(e1['ways'], e2['ways']), 'circuits': min(e1['circuits'], e2['circuits']),
                      'names': e1['names'] | e2['names'], 'coords': path if w1 < w2 else path[::-1]}
            edges[inc[0]] = merged; dead.add(inc[1]); changed = True
            break  # adjacency is stale after a merge
        edges = [e for i, e in enumerate(edges) if i not in dead]

    # 6 / Electrical parameters per branch (all circuits in parallel).
    for e in edges:
        if e['kind'] == 'transformer':
            continue  # sized to local demand in step 8
        else:
            cable = e['kind'] == 'cable'
            zbase = e['kv']**2/BASE_MVA
            e['x_pu'] = (CABLE_X_OHM_KM if cable else X_OHM_KM)[e['kv']]*e['km']/zbase/e['circuits']
            e['rating'] = (CABLE_RATING_MW if cable else RATING_MW)[e['kv']]*e['circuits']

    used = sorted({e['a'] for e in edges} | {e['b'] for e in edges})
    index = {u: i for i, u in enumerate(used)}
    lon = [pos[u][0] for u in used]; lat = [pos[u][1] for u in used]
    kv = {index[u]: node_kv[u] for u in used}  # each bus is one voltage level (transformer edges carry their high side)

    # 7 / Generators: snap stations with known capacity to the nearest bus; interconnectors
    # import at the substation nearest their GB landing.
    gen = defaultdict(lambda: defaultdict(float)); skipped = 0
    nearest = lambda p: min((km(p, (lon[i], lat[i])), i) for i in range(len(used)))
    for s in stations:
        mw = s['properties'].get('capacityMW')
        if not mw or mw < 1: continue
        d, i = nearest(s['geometry']['coordinates'])
        if d > GEN_SNAP_KM: skipped += 1; continue
        gen[i][s['properties']['fuel']] += mw
    imports = []
    for f in links:
        prop = f['properties']
        if prop.get('kind') != 'interconnector' or prop.get('status') != 'operational': continue
        d, i = min(nearest(p) for p in f['geometry']['coordinates'])
        gen[i]['interconnector'] += prop['capMW']
        imports.append({'id': prop['id'], 'bus': i+1, 'mw': prop['capMW'], 'distance_km': round(d, 1)})

    # 8 / Demand: each city split across its three nearest supergrid (275/400 kV) substations,
    # where grid supply points take it, plus a uniform base load at every substation.
    weight = [0.]*len(used)
    supergrid = [i for i in range(len(used)) if kv[i] >= 275]
    for _, x, y, pop in CITIES:
        near = sorted((km((x, y), (lon[i], lat[i])), i) for i in supergrid)[:3]
        inv = [1/(d+3.) for d, _ in near]
        for (d, i), w in zip(near, inv): weight[i] += pop*w/sum(inv)
    total_w = sum(weight)
    # Listed cities hold ~33M of GB's ~67M people, so they take that share of demand; the rest is spread.
    share = [CITY_SHARE*w/total_w + (1-CITY_SHARE)/len(used) for w in weight]
    load = [TOTAL_DEMAND_MW*s for s in share]
    # Transformers: enough units that the site's lower-voltage demand survives losing one (N-1),
    # at least two; peak demand is ~1.5x the 30 GW reference.
    for e in edges:
        if e['kind'] != 'transformer': continue
        unit, x = TRANSFORMER[(e['kv_low'], e['kv'])]
        low = index[e['a']] if kv[index[e['a']]] == e['kv_low'] else index[e['b']]
        e['circuits'] = max(2, math.ceil(1.5*load[low]/unit)+1)
        e['x_pu'] = x*BASE_MVA/unit/e['circuits']; e['rating'] = unit*e['circuits']

    # 9 / Boundary supplies where coverage ends: dead ends with no same-voltage neighbour in reach.
    degree = defaultdict(int)
    for e in edges: degree[index[e['a']]] += 1; degree[index[e['b']]] += 1
    boundary = [i for i in range(len(used)) if degree[i] == 1]
    for i in boundary: gen[i]['boundary'] += round(BOUNDARY_SHARE*load[i], 1)

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

    # 10 / Elevation of every substation and of sample points every ~SAMPLE_KM along overhead routes.
    edges = sorted(edges, key=lambda e: (e['kind'] != 'line', -e['kv'], index[e['a']], index[e['b']]))
    samples = []
    for e in edges:
        n = max(2, round(e['km']/SAMPLE_KM)) if e['kind'] == 'line' else 0
        samples.append(along(e['coords'], [(j+.5)/n for j in range(n)]) if n else [])
    flat = [p for group in samples for p in group]
    heights = elevations([(lon[i], lat[i]) for i in range(len(used))]+flat)
    bus_ele, sample_ele = heights[:len(used)], heights[len(used):]

    buses = [{'id': i+1, 'load_mw': round(load[i], 3), 'lon': round(lon[i], 5), 'lat': round(lat[i], 5),
              'x': project((lon[i], lat[i]))[0], 'y': project((lon[i], lat[i]))[1], 'ele_m': round(bus_ele[i], 1),
              'kv': kv[i], **({'boundary': True} if i in boundary else {})} for i in range(len(used))]
    branches, cursor = [], 0
    prefix = {'line': 'L', 'cable': 'C', 'transformer': 'T', 'link': 'K'}
    count = defaultdict(int)
    for k, e in enumerate(edges):
        names = sorted(e['names'])
        group = samples[k]; ele = sample_ele[cursor:cursor+len(group)]; cursor += len(group)
        ends_ele = [bus_ele[index[e['a']]], bus_ele[index[e['b']]]]
        overhead = e['kind'] == 'line'
        count[e['kind']] += 1
        pylons = max(1, round(e['km']*1000/SPAN_M[e['kv']])) if overhead else 0
        branches.append({'id': f"{prefix[e['kind']]}{count[e['kind']]:03d}", 'kind': e['kind'],
                         'from': index[e['a']]+1, 'to': index[e['b']]+1, 'x_pu': e['x_pu'], 'tap': 1.,
                         'kv': e['kv'], **({'kv_low': e['kv_low']} if 'kv_low' in e else {}), 'length_km': round(e['km'], 2),
                         'pylons': pylons, 'span_m': SPAN_M[e['kv']] if overhead else 0,
                         'tower_height_m': TOWER_HEIGHT_M[e['kv']] if overhead else 0,
                         'ele_max_m': round(max(ele or ends_ele), 1), 'ele_mean_m': round(sum(ele or ends_ele)/len(ele or ends_ele), 1),
                         # Route points (x, y, terrain m); each carries an equal share of the line's pylons.
                         'samples': [project(p)+[round(h, 1)] for p, h in zip(group, ele)],
                         'geometry': [project(p) for p in simplify(e['coords'])], 'circuits': e['circuits'], 'rating_mw': e['rating'],
                         'name': names[0] if names else None,
                         # Longer routes cost more to harden and are more exposed (illustrative).
                         'cost': 1 if e['km'] < 25 else 2 if e['km'] < 60 else 3,
                         'exposure': round(min(.8, .05+.004*e['km']), 4) if overhead else 0.})
    return {'source': 'jacobwright32/uk-grid-atlas src/data/gb (OpenStreetMap power=line, 132-400 kV) plus OSM power=cable '
                      '132-400 kV AC from the dataRefininf grid archive',
            'elevation_source': 'EU-DEM v1.1 (25 m, Copernicus Land Monitoring Service) via OpenTopoData',
            'attribution': meta.get('attribution'), 'atlas_generated': meta.get('generated'),
            'base_mva': BASE_MVA, 'geographic': True, 'buses': buses, 'generators': generators, 'branches': branches,
            'interconnectors': imports,
            'topology': {'cable_ways': len(cables), 'cables_kept': sum(e['kind'] == 'cable' for e in edges), 't_joins_split': joins, 'transformers_added': transformers, 'gap_links_added': gaps,
                         'boundary_supplies': len(boundary), 'overhead_route_km_kept_share': round(kept_share, 3),
                         'circuits_per_tower_route': TOWER_CIRCUITS},
            'notes': (f'{len(overhead_ways)} OSM overhead ways and {len(cables)} underground cable ways clustered at {SNAP_KM} km into substations; {joins} T-joins split; '
                      f'{transformers} transformers and {gaps} dead-end links inferred; series chains contracted; '
                      f'largest component kept ({kept_share:.0%} of overhead route length). Overhead routes assumed '
                      f'double-circuit. {len(boundary)} boundary supplies at coverage edges. {skipped} stations beyond '
                      f'{GEN_SNAP_KM} km skipped. Reactances, thermal ratings, dispatch, demand allocation, costs and '
                      'exposure are illustrative.')}

# Operating points for transformer sizing: (UTC hour, route gust km/h) = evening peak and night
# trough, each calm and windy (wind output reverses many flows).
SIZING_CASES = [('2026-01-01T18:00:00Z', 0.), ('2026-01-01T18:00:00Z', 60.), ('2026-01-01T04:00:00Z', 0.), ('2026-01-01T04:00:00Z', 60.)]


def size_transformers(path, margin=.85, rounds=6):
    """Transformer counts are unknown and OSM cable circuit counts are incomplete, so size both as
    planners would: add units/circuits until each carries at most `margin` of its rating in every
    secured sizing case. Overhead lines are never resized."""
    sys.path.insert(0, str(ROOT))
    import numpy as np
    from backend import simulation
    from backend.grid_ops import demand_factor, secure_dispatch, solve
    data = json.loads(path.read_text()); added = {'transformer': 0, 'cable': 0}
    for _ in range(rounds):
        simulation.NETWORKS['_build'] = str(path)
        net = simulation.Network('_build')
        full = np.ones(net.m); peak_flow = np.zeros(net.m)
        for stamp, gust in SIZING_CASES:
            demand = net.load*demand_factor([stamp], net.load.sum())[0]
            variable, firm, sync = net.grid_model.availability(np.full(net.m, gust), stamp)
            flows = solve(net, full, demand, variable, firm, sync, secure_dispatch(net, full, demand, variable, firm, sync))['flows']
            peak_flow = np.maximum(peak_flow, np.abs(flows))
        flows = peak_flow
        changed = False
        for i, e in enumerate(data['branches']):
            if e['kind'] not in ('transformer', 'cable') or abs(flows[i]) <= margin*e['rating_mw']: continue
            unit = e['rating_mw']/e['circuits']; per_unit_x = e['x_pu']*e['circuits']
            extra = math.ceil(abs(flows[i])/margin/unit)-e['circuits']
            e['circuits'] += extra; e['rating_mw'] = unit*e['circuits']; e['x_pu'] = per_unit_x/e['circuits']
            added[e['kind']] += extra; changed = True
        path.write_text(json.dumps(data, separators=(',', ':')))
        if not changed: break
    data['topology']['transformer_units_added_for_peak'] = added['transformer']
    data['topology']['cable_circuits_added_for_peak'] = added['cable']
    path.write_text(json.dumps(data, separators=(',', ':')))
    return added


if __name__ == '__main__':
    out = build(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT.parent/'uk-grid-atlas'),
                Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT/'data/grid/uk_power_grid.sqlite')
    (ROOT/'data/gb_network.json').write_text(json.dumps(out, separators=(',', ':')))
    added = size_transformers(ROOT/'data/gb_network.json')
    out['topology'].update(transformer_units_added_for_peak=added['transformer'], cable_circuits_added_for_peak=added['cable'])
    cap = sum(g['max_mw'] for g in out['generators'])
    print(f"{len(out['buses'])} buses, {len(out['branches'])} branches, {len(out['generators'])} generator buses, "
          f"{cap:.0f} MW capacity, {sum(b['load_mw'] for b in out['buses']):.0f} MW demand; {out['topology']}")
