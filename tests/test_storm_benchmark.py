"""Real-storm benchmark builder (scripts/build_storm_benchmark.py) and storm listing."""
import csv
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app import app
from backend.simulation import Network

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_storm_benchmark', ROOT/'scripts/build_storm_benchmark.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

START = datetime(2024, 2, 5, tzinfo=timezone.utc)
STORM = {'id': 'test-2024', 'name': 'Test storm', 'start': START.isoformat(), 'hours': 24}
HOME = (-2.5, 52.5)    # NGED: a daily fault here all year gives the cell a normal rate
OTHER = (0.5, 51.5)    # UKPN


def write_csv(path, ukpn_from):
    stamp = lambda d: d.strftime('%Y-%m-%dT%H:%M:%SZ')
    rows = []
    def add(op, src, start, end, lon, lat, basis='reported'):
        rows.append({'record_id': f'{op}:{len(rows)}', 'source': src, 'operator': op, 'start_time_utc': stamp(start),
                     'end_time_utc': stamp(end) if end else '', 'start_time_basis': basis, 'lon': lon, 'lat': lat})
    for day in range(60):  # NGED normal days, 1 Jan - 29 Feb
        t = datetime(2024, 1, 1, 9, tzinfo=timezone.utc)+timedelta(days=day)
        add('NGED', 'nationalgrid-outage-history', t, t+timedelta(hours=2), *HOME)
    first_ukpn = datetime(2024, 1, 1, tzinfo=timezone.utc) if ukpn_from == 'early' else datetime(2024, 2, 10, tzinfo=timezone.utc)
    for day in range(60):  # runs past the storm either way
        t = first_ukpn+timedelta(days=day, hours=12)
        add('UKPN', 'ukpn_powercuts', t, t+timedelta(hours=1), *OTHER)
    # Storm: a fault that ends at hour 5, re-issued under a new id at hour 6 at the same spot.
    add('NGED', 'nationalgrid-outage-history', START+timedelta(hours=3), START+timedelta(hours=5), -3.1, 51.6)
    add('NGED', 'nationalgrid-outage-history', START+timedelta(hours=6), START+timedelta(hours=8), -3.1, 51.6)
    # Unknown start (first seen already restored) and other sources are not usable.
    add('NGED', 'nationalgrid-outage-history', START+timedelta(hours=7), None, -3.2, 51.7, 'unknown(first_seen_already_restored)')
    add('WPD', 'Outage-Watch', START+timedelta(hours=7), None, -3.3, 51.7)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)


@pytest.fixture
def no_weather_calls(monkeypatch):
    monkeypatch.setattr(builder, 'fetch_gusts', lambda storm, points, start, hours: {p: (0., [80.]*hours) for p in points})


def test_builder_uses_only_feeds_covering_the_storm(tmp_path, no_weather_calls):
    write_csv(tmp_path/'o.csv', ukpn_from='late')
    out = builder.build(STORM, builder.read_outages(tmp_path/'o.csv'))
    assert out['operators'] == ['NGED'] and out['operator'] == 'NGED'
    # The storm-day NGED fault at HOME (09:00) plus the first of the re-issued pair; the re-issue is dropped.
    assert sorted(o[2] for o in out['outages']) == [3, 9] and out['reissued_dropped'] == 1
    (feed,) = out['baseline']['feeds']
    assert feed['operator'] == 'NGED'
    home = builder.cell(*HOME, builder.GRID)
    # 60 daily faults, minus the 15 days within a week of the storm (29 Jan - 12 Feb).
    assert [n for cx, cy, n in feed['counts'] if (cx, cy) == home] == [45]
    assert all(g == 80. for hour in out['grid']['gust_kmh'] for row in hour for g in row if g is not None)
    assert len(out['timestamps']) == 24


def test_builder_combines_feeds(tmp_path, no_weather_calls):
    write_csv(tmp_path/'o.csv', ukpn_from='early')
    out = builder.build(STORM, builder.read_outages(tmp_path/'o.csv'))
    assert out['operators'] == ['NGED', 'UKPN'] and out['operator'] == 'NGED and UKPN'
    assert [f['operator'] for f in out['baseline']['feeds']] == ['NGED', 'UKPN']
    coverage = {tuple(c) for c in out['coverage']}
    assert builder.cell(*HOME, builder.COVERAGE_CELL) in coverage and builder.cell(*OTHER, builder.COVERAGE_CELL) in coverage


def test_normal_rate_sums_each_feed_over_its_own_span(tmp_path, no_weather_calls, monkeypatch):
    from backend import benchmark
    write_csv(tmp_path/'o.csv', ukpn_from='early')
    storm = builder.build(STORM, builder.read_outages(tmp_path/'o.csv'))
    storm['baseline']['feeds'].append({'operator': 'EXTRA', 'days': 10., 'counts': [[*builder.cell(*HOME, builder.GRID), 5]]})
    monkeypatch.setattr(benchmark, 'load_storm', lambda storm_id: storm)
    gb = Network('gb')
    home = builder.cell(*HOME, builder.GRID)
    nged = next(f for f in storm['baseline']['feeds'] if f['operator'] == 'NGED')
    n_home = next(n for cx, cy, n in nged['counts'] if (cx, cy) == home)
    cells = benchmark.run_real_storm(gb, 'test-2024', 1)['benchmark']['cells']
    lon, lat = (home[0]+.5)*builder.GRID, (home[1]+.5)*builder.GRID
    x, _ = benchmark.Projection(gb).xy(lon, lat)
    (row,) = [c for c in cells if abs(c['x']-round(float(x), 5)) < 1e-9]
    assert row['expected_normal'] == pytest.approx(n_home/nged['days']+5/10., abs=.01)


def test_storm_list_matches_files_on_disk():
    files = sorted((ROOT/'data/storms').glob('*.json'))
    built = [s for s in (json.loads(p.read_text(encoding='utf-8')) for p in files) if isinstance(s, dict) and 'outages' in s]
    body = app.test_client().get('/api/storms').get_json()
    # Darragh is tracked in git; other storms exist only where they were built.
    assert 'darragh-2024' in {s['id'] for s in body['storms']}
    assert sorted(s['id'] for s in body['storms']) == sorted(s['id'] for s in built)
    assert [s['start'] for s in body['storms']] == sorted(s['start'] for s in body['storms'])
    assert len(body['network']['buses']) == len(json.loads((ROOT/'data/gb_network.json').read_text())['buses'])
    for entry in body['storms']:
        storm = next(s for s in built if s['id'] == entry['id'])
        assert entry['outages'] == len(storm['outages']) and entry['operators'] == storm['operators']
        assert entry['id'] in {s['id'] for s in builder.CATALOGUE}
