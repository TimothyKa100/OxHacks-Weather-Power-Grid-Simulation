"""Build data/storms/darragh_2024.json: real outages + real hourly gusts for Storm Darragh.

Usage: python scripts/build_storm_benchmark.py /path/to/processed/unplanned_outages.csv

Outages: NGED unplanned power cuts (git-scraped feed, see the outage dataset's README).
The feed only covers NGED's licence areas (South West, South Wales, East/West Midlands),
so "no outage" elsewhere means "no data", not "no fault". These are distribution
faults, not transmission pylons, so the benchmark compares where and when the
simulated risk lands, not absolute counts.

Weather: Met Office UKV (2 km, via the ukmo_seamless model) hourly 10 m gusts from
the Open-Meteo historical forecast API, sampled on a 0.25 degree grid.
"""
import csv
import json
import math
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'data/storms/darragh_2024.json'
CACHE = ROOT/'data/storms/.ukv_cache.json'
CACHE.parent.mkdir(exist_ok=True)
STORM = {'id': 'darragh-2024', 'name': 'Storm Darragh', 'start': '2024-12-06T12:00:00+00:00', 'hours': 60}
OPERATOR = 'NGED'
GRID = .25                     # weather grid spacing (degrees)
COVERAGE_CELL = .1             # cells where NGED ever reported an outage = data coverage
BASELINE_EXCLUDE_DAYS = 7      # days either side of the storm left out of the normal-rate baseline
REID_HOURS = 4                 # same location restarting within this window = feed re-issued the id
API = 'https://historical-forecast-api.open-meteo.com/v1/forecast'


def parse(t): return datetime.fromisoformat(t.replace('Z', '+00:00')) if t else None


def cell(lon, lat, size): return (math.floor(lon/size), math.floor(lat/size))


def fetch_gusts(points, start, hours):
    """{(lon, lat): (model elevation m, [gust km/h per hour])} from UKV via Open-Meteo, cached."""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    first, last = start.date(), (start+timedelta(hours=hours-1)).date()
    key = lambda p: f'{p[1]:.3f},{p[0]:.3f}'
    todo = [p for p in points if key(p) not in cache]
    for i in range(0, len(todo), 50):
        batch = todo[i:i+50]
        query = (f"{API}?latitude={','.join(f'{p[1]:.3f}' for p in batch)}&longitude={','.join(f'{p[0]:.3f}' for p in batch)}"
                 f"&start_date={first}&end_date={last}&hourly=wind_gusts_10m&models=ukmo_seamless&wind_speed_unit=kmh&timezone=GMT")
        for attempt in range(8):
            try:
                with urllib.request.urlopen(query, timeout=60) as r: rows = json.load(r)
                break
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == 7: raise
                print(f'  weather API: {exc}; retrying in 60s', file=sys.stderr); time.sleep(60)
        for p, row in zip(batch, rows if isinstance(rows, list) else [rows]):
            cache[key(p)] = {'elevation': row.get('elevation'), 'time': row['hourly']['time'], 'gust': row['hourly']['wind_gusts_10m']}
        CACHE.write_text(json.dumps(cache, separators=(',', ':')))
        print(f'  weather {min(i+50, len(todo))}/{len(todo)}', file=sys.stderr)
        time.sleep(6)
    out = {}
    for p in points:
        c = cache[key(p)]
        index = {datetime.fromisoformat(t).replace(tzinfo=timezone.utc): g for t, g in zip(c['time'], c['gust'])}
        out[p] = (c['elevation'] or 0., [index.get(start+timedelta(hours=h)) for h in range(hours)])
    return out


def build(outages_csv):
    start = parse(STORM['start']); end = start+timedelta(hours=STORM['hours'])
    rows = []
    with open(outages_csv, newline='') as f:
        for r in csv.DictReader(f):
            if r['operator'] != OPERATOR or not r['lat'] or not r['start_time_utc']: continue
            rows.append((parse(r['start_time_utc']), parse(r['end_time_utc']), float(r['lon']), float(r['lat']), r['record_id']))

    # Coverage: where this feed has ever reported anything.
    coverage = sorted({cell(lon, lat, COVERAGE_CELL) for _, _, lon, lat, _ in rows})
    # Normal rate: outages per weather cell per day, excluding a fortnight around the storm.
    t0, t1 = min(r[0] for r in rows), max(r[0] for r in rows)
    lo, hi = start-timedelta(days=BASELINE_EXCLUDE_DAYS), end+timedelta(days=BASELINE_EXCLUDE_DAYS)
    baseline = Counter(cell(lon, lat, GRID) for s, _, lon, lat, _ in rows if not lo <= s < hi)
    baseline_days = ((t1-t0)-(hi-lo)).total_seconds()/86400

    # Storm outages, dropping ids the feed re-issued for faults already running at that spot.
    ended = defaultdict(list)
    for s, e, lon, lat, _ in rows:
        if e and start-timedelta(hours=REID_HOURS) <= e < end: ended[(round(lon, 4), round(lat, 4))].append(e)
    storm, reissued = [], 0
    for s, e, lon, lat, rid in sorted(rows):
        if not start <= s < end: continue
        if any(timedelta(0) <= s-x <= timedelta(hours=REID_HOURS) for x in ended[(round(lon, 4), round(lat, 4))]):
            reissued += 1; continue
        storm.append([round(lon, 5), round(lat, 5), int((s-start).total_seconds()//3600),
                      round((e-s).total_seconds()/60) if e else None])

    # Weather grid: every 0.25 degree point over GB land that matters (near coverage or the grid).
    network = json.loads((ROOT/'data/gb_network.json').read_text())
    near = {cell(lon+dx*COVERAGE_CELL, lat+dy*COVERAGE_CELL, GRID) for b in network['buses']
            for lon, lat in [(b['lon'], b['lat'])] for dx in (-3, 0, 3) for dy in (-3, 0, 3)}
    near |= {cell((cx+.5)*COVERAGE_CELL, (cy+.5)*COVERAGE_CELL, GRID) for cx, cy in coverage}
    lons = [round(-8.5+GRID*i, 3) for i in range(int((2.25+8.5)/GRID)+1)]
    lats = [round(49.75+GRID*j, 3) for j in range(int((59.25-49.75)/GRID)+1)]
    # Grid nodes are cell corners; keep a node if any of its four neighbouring cells is needed.
    points = [(x, y) for y in lats for x in lons
              if any((cell(x, y, GRID)[0]-dx, cell(x, y, GRID)[1]-dy) in near for dx in (0, 1) for dy in (0, 1))]
    print(f'{len(storm)} storm outages ({reissued} re-issued ids dropped); fetching {len(points)} weather points', file=sys.stderr)
    weather = fetch_gusts(points, start, STORM['hours'])
    gust = [[None]*len(lons) for _ in lats]; model_ele = [[None]*len(lons) for _ in lats]
    series = {}
    for (x, y), (ele, g) in weather.items():
        j, i = lats.index(y), lons.index(x); model_ele[j][i] = ele; series[(j, i)] = g
    gust_hourly = [[[series[(j, i)][h] if (j, i) in series else None for i in range(len(lons))] for j in range(len(lats))]
                   for h in range(STORM['hours'])]

    return {**STORM, 'operator': OPERATOR,
            'sources': {'outages': 'NGED unplanned power cuts, git-scraped feed (jmcharter/nationalgrid-outage-history)',
                        'weather': 'Met Office UKV via Open-Meteo historical forecast API (ukmo_seamless), hourly 10 m gusts',
                        'weather_attribution': 'Weather data by Open-Meteo.com (CC BY 4.0); Met Office data © Crown copyright (OGL)'},
            'timestamps': [(start+timedelta(hours=h)).isoformat() for h in range(STORM['hours'])],
            'outages': storm, 'reissued_dropped': reissued,
            'coverage_cell': COVERAGE_CELL, 'coverage': [list(c) for c in coverage],
            'grid': {'step': GRID, 'lons': lons, 'lats': lats, 'model_elevation_m': model_ele, 'gust_kmh': gust_hourly},
            'baseline': {'cell': GRID, 'days': round(baseline_days, 1), 'counts': [[k[0], k[1], v] for k, v in sorted(baseline.items())]}}


if __name__ == '__main__':
    out = build(sys.argv[1] if len(sys.argv) > 1 else ROOT.parent/'data/processed/unplanned_outages.csv')
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(',', ':')))
    print(f"wrote {OUT.relative_to(ROOT)}: {len(out['outages'])} outages, {len(out['coverage'])} coverage cells, "
          f"{OUT.stat().st_size/1e6:.1f} MB")
