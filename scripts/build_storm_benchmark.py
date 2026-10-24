"""Build data/storms/<id>.json for every storm in CATALOGUE: real outages + real hourly gusts.

Usage: python scripts/build_storm_benchmark.py [outages.csv] [--storm ID ...]
       (default CSV: data/outages/unplanned_outages.csv)

Outages: unplanned power cuts from the git-scraped distribution-network feeds in the CSV
(FEEDS below). A storm uses every feed that was running for the whole storm window. Each
feed only covers its operator's licence areas (NGED: South West, South Wales, East/West
Midlands; UKPN: London, South East, East of England), so "no outage" elsewhere means
"no data", not "no fault". These are distribution faults, not transmission pylons, so the
benchmark compares where and when the simulated risk lands, not absolute counts.

Weather: Met Office UKV (2 km, via the ukmo_seamless model) hourly 10 m gusts from
the Open-Meteo historical forecast API, sampled on a 0.25 degree grid. Responses are cached
per storm in data/storms/.cache/, so reruns only fetch missing points. The free API allows
5,000 location-calls an hour, so requests are paced.

Writes data/storms/index.json, which the backend reads to list the storms.
"""
import csv
import http.client
import json
import math
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORMS_DIR = ROOT/'data/storms'
CACHE_DIR = STORMS_DIR/'.cache'
DEFAULT_CSV = ROOT/'data/outages/unplanned_outages.csv'

# Named storms (Met Office storm centre impact dates) with a clear outage spike in the CSV.
# Windows are UTC and cover the impact days.
CATALOGUE = [
    {'id': 'bert-2024', 'name': 'Storm Bert', 'start': '2024-11-23T00:00:00+00:00', 'hours': 72},
    {'id': 'darragh-2024', 'name': 'Storm Darragh', 'start': '2024-12-06T12:00:00+00:00', 'hours': 60},
    {'id': 'eowyn-2025', 'name': 'Storm Éowyn', 'start': '2025-01-23T12:00:00+00:00', 'hours': 48},
    {'id': 'amy-2025', 'name': 'Storm Amy', 'start': '2025-10-03T00:00:00+00:00', 'hours': 48},
    {'id': 'claudia-2025', 'name': 'Storm Claudia', 'start': '2025-11-13T12:00:00+00:00', 'hours': 48},
    {'id': 'bram-2025', 'name': 'Storm Bram', 'start': '2025-12-08T12:00:00+00:00', 'hours': 48},
    {'id': 'goretti-2026', 'name': 'Storm Goretti', 'start': '2026-01-08T00:00:00+00:00', 'hours': 48},
    {'id': 'austen-2026', 'name': 'Storm Austen', 'start': '2026-10-22T12:00:00+00:00', 'hours': 36},
]
# Operator -> source column value for the continuously scraped feeds. The 2021-22
# Outage-Watch rows are too sparse to give a normal rate.
FEEDS = {'NGED': 'nationalgrid-outage-history', 'UKPN': 'ukpn_powercuts'}
FEED_NAMES = {'NGED': 'NGED unplanned power cuts (git-scraped feed, jmcharter/nationalgrid-outage-history)',
              'UKPN': 'UK Power Networks unplanned power cuts (ukpn_powercuts feed)'}
GRID = .25                     # weather grid spacing (degrees)
COVERAGE_CELL = .1             # cells where a feed ever reported an outage = data coverage
BASELINE_EXCLUDE_DAYS = 7      # days either side of the storm left out of the normal-rate baseline
REID_HOURS = 4                 # same location restarting within this window = feed re-issued the id
API = 'https://historical-forecast-api.open-meteo.com/v1/forecast'
BATCH = 50                     # locations per request
PAUSE_S = 40                   # between requests: 50 locations / 40 s = 4,500 location-calls an hour


def parse(t): return datetime.fromisoformat(t.replace('Z', '+00:00')) if t else None


def cell(lon, lat, size): return (math.floor(lon/size), math.floor(lat/size))


def seed_cache(storm, cache):
    """Reuse gusts already stored in an earlier build of this storm, so it costs no API calls."""
    previous = STORMS_DIR/f"{storm['id']}.json"
    if cache or not previous.exists(): return
    old = json.loads(previous.read_text(encoding='utf-8', errors='replace'))  # older builds were not always UTF-8
    if old.get('start') != storm['start'] or old.get('hours') != storm['hours']: return
    g = old['grid']
    times = [datetime.fromisoformat(t).strftime('%Y-%m-%dT%H:%M') for t in old['timestamps']]
    for j, lat in enumerate(g['lats']):
        for i, lon in enumerate(g['lons']):
            if g['model_elevation_m'][j][i] is None: continue
            cache[f'{lat:.3f},{lon:.3f}'] = {'elevation': g['model_elevation_m'][j][i], 'time': times,
                                             'gust': [g['gust_kmh'][h][j][i] for h in range(storm['hours'])]}


def fetch_gusts(storm, points, start, hours):
    """{(lon, lat): (model elevation m, [gust km/h per hour])} from UKV via Open-Meteo, cached per storm."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR/f"{storm['id']}.json"
    cache = json.loads(cache_file.read_text(encoding='utf-8')) if cache_file.exists() else {}
    seed_cache(storm, cache)
    first, last = start.date(), (start+timedelta(hours=hours-1)).date()
    key = lambda p: f'{p[1]:.3f},{p[0]:.3f}'
    todo = [p for p in points if key(p) not in cache]
    print(f"  {storm['id']}: {len(points)-len(todo)} weather points cached, {len(todo)} to fetch", file=sys.stderr)
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i+BATCH]
        query = (f"{API}?latitude={','.join(f'{p[1]:.3f}' for p in batch)}&longitude={','.join(f'{p[0]:.3f}' for p in batch)}"
                 f"&start_date={first}&end_date={last}&hourly=wind_gusts_10m&models=ukmo_seamless&wind_speed_unit=kmh&timezone=GMT")
        for attempt in range(16):
            try:
                with urllib.request.urlopen(query, timeout=60) as r: rows = json.load(r)
                break
            # OSError covers URLError, timeouts and dropped connections; ValueError an empty or cut-off body.
            except (OSError, ValueError, http.client.HTTPException) as exc:
                if attempt == 15: raise
                # 429 = hourly/daily quota: wait it out rather than give up.
                wait = 300 if getattr(exc, 'code', None) == 429 else 60
                print(f'  weather API: {exc}; retrying in {wait}s', file=sys.stderr); time.sleep(wait)
        for p, row in zip(batch, rows if isinstance(rows, list) else [rows]):
            cache[key(p)] = {'elevation': row.get('elevation'), 'time': row['hourly']['time'], 'gust': row['hourly']['wind_gusts_10m']}
        cache_file.write_text(json.dumps(cache, separators=(',', ':')), encoding='utf-8')
        print(f"  {storm['id']} weather {min(i+BATCH, len(todo))}/{len(todo)}", file=sys.stderr)
        if i+BATCH < len(todo): time.sleep(PAUSE_S)
    out = {}
    for p in points:
        c = cache[key(p)]
        index = {datetime.fromisoformat(t).replace(tzinfo=timezone.utc): g for t, g in zip(c['time'], c['gust'])}
        out[p] = (c['elevation'] or 0., [index.get(start+timedelta(hours=h)) for h in range(hours)])
    return out


def read_outages(outages_csv):
    """Rows per feed: (start, end, lon, lat, record id)."""
    rows = defaultdict(list)
    with open(outages_csv, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            op = r['operator']
            if FEEDS.get(op) != r['source'] or not r['lat'] or not r['start_time_utc']: continue
            if r['start_time_basis'].startswith('unknown'): continue  # first seen already restored: start unknown
            rows[op].append((parse(r['start_time_utc']), parse(r['end_time_utc']), float(r['lon']), float(r['lat']), r['record_id']))
    return rows


def build(storm, feeds):
    start = parse(storm['start']); end = start+timedelta(hours=storm['hours'])
    lo, hi = start-timedelta(days=BASELINE_EXCLUDE_DAYS), end+timedelta(days=BASELINE_EXCLUDE_DAYS)
    # Feeds running for the whole window, with a week of history before it.
    operators = [op for op, rows in feeds.items() if min(r[0] for r in rows) <= start-timedelta(days=BASELINE_EXCLUDE_DAYS)
                 and max(r[0] for r in rows) >= end]
    if not operators: raise ValueError(f"no outage feed covers {storm['id']}")

    coverage, baseline_feeds, storm_rows, reissued = set(), [], [], 0
    for op in operators:
        rows = feeds[op]
        # Coverage: where this feed has ever reported anything.
        coverage |= {cell(lon, lat, COVERAGE_CELL) for _, _, lon, lat, _ in rows}
        # Normal rate: outages per weather cell per day over the feed's own span, excluding a fortnight around the storm.
        t0, t1 = min(r[0] for r in rows), max(r[0] for r in rows)
        counts = Counter(cell(lon, lat, GRID) for s, _, lon, lat, _ in rows if not lo <= s < hi)
        days = ((t1-t0)-(min(hi, t1)-max(lo, t0))).total_seconds()/86400
        baseline_feeds.append({'operator': op, 'days': round(days, 1), 'counts': [[k[0], k[1], v] for k, v in sorted(counts.items())]})
        # Storm outages, dropping ids the feed re-issued for faults already running at that spot.
        ended = defaultdict(list)
        for s, e, lon, lat, _ in rows:
            if e and start-timedelta(hours=REID_HOURS) <= e < end: ended[(round(lon, 4), round(lat, 4))].append(e)
        for s, e, lon, lat, rid in sorted(rows):
            if not start <= s < end: continue
            if any(timedelta(0) <= s-x <= timedelta(hours=REID_HOURS) for x in ended[(round(lon, 4), round(lat, 4))]):
                reissued += 1; continue
            storm_rows.append([round(lon, 5), round(lat, 5), int((s-start).total_seconds()//3600),
                               round((e-s).total_seconds()/60) if e else None])
    storm_rows.sort(key=lambda o: (o[2], o[0], o[1]))
    coverage = sorted(coverage)

    # Weather grid: every 0.25 degree point over GB land that matters (near coverage or the grid).
    network = json.loads((ROOT/'data/gb_network.json').read_text(encoding='utf-8'))
    near = {cell(lon+dx*COVERAGE_CELL, lat+dy*COVERAGE_CELL, GRID) for b in network['buses']
            for lon, lat in [(b['lon'], b['lat'])] for dx in (-3, 0, 3) for dy in (-3, 0, 3)}
    near |= {cell((cx+.5)*COVERAGE_CELL, (cy+.5)*COVERAGE_CELL, GRID) for cx, cy in coverage}
    lons = [round(-8.5+GRID*i, 3) for i in range(int((2.25+8.5)/GRID)+1)]
    lats = [round(49.75+GRID*j, 3) for j in range(int((59.25-49.75)/GRID)+1)]
    # Grid nodes are cell corners; keep a node if any of its four neighbouring cells is needed.
    points = [(x, y) for y in lats for x in lons
              if any((cell(x, y, GRID)[0]-dx, cell(x, y, GRID)[1]-dy) in near for dx in (0, 1) for dy in (0, 1))]
    print(f"{storm['name']}: {len(storm_rows)} outages from {', '.join(operators)} ({reissued} re-issued ids dropped)", file=sys.stderr)
    weather = fetch_gusts(storm, points, start, storm['hours'])
    gust = [[None]*len(lons) for _ in lats]; model_ele = [[None]*len(lons) for _ in lats]
    series = {}
    for (x, y), (ele, g) in weather.items():
        j, i = lats.index(y), lons.index(x); model_ele[j][i] = ele; series[(j, i)] = g
    gust_hourly = [[[series[(j, i)][h] if (j, i) in series else None for i in range(len(lons))] for j in range(len(lats))]
                   for h in range(storm['hours'])]

    return {**storm, 'operator': ' and '.join(operators), 'operators': operators,
            'sources': {'outages': '; '.join(FEED_NAMES[op] for op in operators)+' (data/outages/unplanned_outages.csv)',
                        'weather': 'Met Office UKV via Open-Meteo historical forecast API (ukmo_seamless), hourly 10 m gusts',
                        'weather_attribution': 'Weather data by Open-Meteo.com (CC BY 4.0); Met Office data © Crown copyright (OGL)'},
            'timestamps': [(start+timedelta(hours=h)).isoformat() for h in range(storm['hours'])],
            'outages': storm_rows, 'reissued_dropped': reissued,
            'coverage_cell': COVERAGE_CELL, 'coverage': [list(c) for c in coverage],
            'grid': {'step': GRID, 'lons': lons, 'lats': lats, 'model_elevation_m': model_ele, 'gust_kmh': gust_hourly},
            'baseline': {'cell': GRID, 'feeds': baseline_feeds}}


def write_index():
    """List every built storm for the backend, in date order."""
    index = []
    for storm in CATALOGUE:
        path = STORMS_DIR/f"{storm['id']}.json"
        if not path.exists(): continue
        built = json.loads(path.read_text(encoding='utf-8'))
        peak = max((g for hour in built['grid']['gust_kmh'] for row in hour for g in row if g is not None), default=None)
        index.append({**storm, 'file': f"data/storms/{storm['id']}.json", 'operators': built['operators'],
                      'outages': len(built['outages']), 'peak_gust_kmh': round(peak) if peak is not None else None})
    # ASCII-escaped JSON reads correctly whatever the reader's locale encoding.
    (STORMS_DIR/'index.json').write_text(json.dumps(index, indent=1)+'\n', encoding='utf-8')
    return index


if __name__ == '__main__':
    args = sys.argv[1:]
    wanted = {args[i+1] for i, a in enumerate(args) if a == '--storm' and i+1 < len(args)}
    positional = [a for i, a in enumerate(args) if a != '--storm' and (i == 0 or args[i-1] != '--storm')]
    unknown = wanted-{s['id'] for s in CATALOGUE}
    if unknown: sys.exit(f"unknown storm id(s): {', '.join(sorted(unknown))}")
    feeds = read_outages(positional[0] if positional else DEFAULT_CSV)
    STORMS_DIR.mkdir(exist_ok=True)
    for storm in CATALOGUE:
        if wanted and storm['id'] not in wanted: continue
        out = build(storm, feeds)
        path = STORMS_DIR/f"{storm['id']}.json"
        path.write_text(json.dumps(out, separators=(',', ':')), encoding='utf-8')  # ASCII-escaped, see write_index
        print(f"wrote {path.relative_to(ROOT)}: {len(out['outages'])} outages, {len(out['coverage'])} coverage cells, "
              f"{path.stat().st_size/1e6:.1f} MB", file=sys.stderr)
    print(f'index: {len(write_index())} storms', file=sys.stderr)
