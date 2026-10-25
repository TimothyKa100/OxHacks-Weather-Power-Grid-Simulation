import numpy as np
import pytest
from backend.simulation import Network, simulate, evaluate_many
from backend.weather import multiday_scenarios
from backend.app import app


@pytest.fixture(scope='module')
def gb(): return Network('gb')


def test_gb_backbone_is_connected_and_balanced(gb):
    assert gb.geographic and gb.n > 400 and gb.m > 600
    assert {e['kv'] for e in gb.branches} == {132, 275, 400}
    assert {e['kind'] for e in gb.branches} == {'line', 'cable', 'transformer', 'link'}
    intact = gb.solve(tuple([True]*gb.m), tuple(gb.load))
    assert len(intact['islands']) == 1
    assert intact['served'].sum() == pytest.approx(gb.load.sum())
    # Ratings are voltage class x circuits (no uplift to the dispatch); the intact grid fits them.
    assert np.allclose(gb.ratings, [e['rating_mw'] for e in gb.branches])
    assert np.max(np.abs(intact['flows'])/gb.ratings) < 1
    assert all(49.5 < b['lat'] < 61 and -8.5 < b['lon'] < 2 for b in gb.buses)


def test_gb_storm_is_spatial(gb):
    s = multiday_scenarios(gb, 100, 26, 1, hours=48)[0]
    peak = s['gust'].max(axis=0)
    # A moving depression: some routes see near-peak gusts, others stay mild.
    assert peak.max() > 90 and peak.min() < 50
    s['draws'][:] = 1
    assert simulate(gb, s)['unserved_mwh'] == pytest.approx(0, abs=1e-6)


def test_parallel_evaluation_matches_serial(gb):
    bank = multiday_scenarios(gb, 110, 3, 3, hours=24, crews=6)
    parallel = evaluate_many(gb, bank, [[], [0, 1]])
    serial = [[simulate(gb, s, p) for s in bank] for p in ([], [0, 1])]
    for a, b in zip(parallel, serial):
        assert [r['unserved_mwh'] for r in a] == [r['unserved_mwh'] for r in b]


def test_api_network_selection():
    client = app.test_client()
    assert client.get('/api/network?name=gb').get_json()['geographic'] is True
    assert client.get('/api/network?name=nope').status_code == 400
    assert client.post('/api/experiment', json={'network': 'nope'}).status_code == 400


def test_pylon_count_drives_line_risk(gb):
    from backend.storm import pylon_failure_probability, PYLON_MEDIAN_GUST
    p = pylon_failure_probability(np.array([100, 150, 200, PYLON_MEDIAN_GUST]))
    assert np.all(np.diff(p) > 0) and p[-1] == pytest.approx(.5)
    lines = [e for e in gb.branches if e['kind'] == 'line']
    lengths = np.array([e['length_km'] for e in lines]); pylons = np.array([e['pylons'] for e in lines])
    assert np.all(pylons >= 1) and np.corrcoef(lengths, pylons)[0, 1] > .99
    # Cables, transformers and inferred links have no towers, so no wind collapse risk.
    assert all(e['pylons'] == 0 and not e['samples'] and e['exposure'] == 0 for e in gb.branches if e['kind'] != 'line')


def test_storm_run_is_reproducible_and_physical(gb):
    from backend.storm import run_storm
    a, b = run_storm(gb, 190, 24, 7), run_storm(gb, 190, 24, 7)
    assert a == b
    frames = a['frames']
    assert len(frames) == 25 and frames[0]['shed_mw'] == 0
    fallen = [i for i, s in enumerate(frames[-1]['status']) if s == 'pylon']
    assert fallen and all(len(frames[-1]['fallen_at'][i]) == 3 for i in fallen)
    # The fallen pylon sits on one of that line's own route samples.
    for i in fallen:
        x, y, ele = frames[-1]['fallen_at'][i]
        assert any(abs(x-sx) < 1e-4 and abs(y-sy) < 1e-4 and ele == se for sx, sy, se in gb.branches[i]['samples'])
    # Fallen lines never come back; settled frames are within ratings.
    for f in frames: assert max(l for l, s in zip(f['loading'], f['status']) if s == 'online') <= 1+1e-7
    for f, g in zip(frames, frames[1:]):
        assert all(g['status'][i] == 'pylon' for i, s in enumerate(f['status']) if s == 'pylon')
    calm = run_storm(gb, 100, 24, 7)['frames']
    assert all(s == 'online' for s in calm[-1]['status']) and calm[-1]['shed_mw'] == 0


def test_storm_api():
    client = app.test_client()
    ok = client.post('/api/storm', json={'peak_gust': 150, 'hours': 12, 'seed': 3})
    assert ok.status_code == 200 and len(ok.get_json()['frames']) == 13
    assert client.post('/api/storm', json={'peak_gust': 999}).status_code == 400


def test_every_node_and_route_sample_has_elevation(gb):
    assert all(0 <= b['ele_m'] < 1400 for b in gb.buses)
    assert all(e['samples'] and all(0 <= s[2] < 1400 for s in e['samples']) for e in gb.branches if e['kind'] == 'line')
    # Scotland's 132-275 kV routes cross real uplands.
    assert max(e['ele_max_m'] for e in gb.branches) > 400
    assert 'samples' not in gb.payload()['branches'][0]


def test_elevation_raises_gust_and_collapse_risk():
    from backend.storm import altitude_factor, pylon_failure_probability
    assert altitude_factor(0, 50) == 1
    assert altitude_factor(400, 50) == pytest.approx(1+.4*(10/50)**.2)
    assert altitude_factor(400, 27) > altitude_factor(400, 50)  # wind speed-up is diluted for taller towers
    sea, hill = pylon_failure_probability(170*altitude_factor(np.array([0, 400]), 50))
    assert hill > 20*sea


def test_darragh_benchmark(gb):
    from backend.benchmark import run_real_storm, bilinear
    r = run_real_storm(gb, 'darragh-2024', 1)
    b = r['benchmark']
    assert len(r['frames']) == r['config']['hours']+1 == len(r['storm']['timestamps'])+1
    assert sum(b['observed_per_hour']) == len(r['outages']) > 1000
    assert all(v >= 0 for v in b['modelled_per_hour']) and max(b['modelled_per_hour']) > 0
    assert -1 <= b['timing_correlation'] <= 1 and -1 <= b['place_rank_correlation'] <= 1
    assert len(b['cells']) > 20 and b['rise_windiest_third'] > 0
    assert run_real_storm(gb, 'darragh-2024', 1)['benchmark'] == b
    # Bilinear interpolation reproduces grid nodes and skips missing corners.
    grid = np.array([[1., 2.], [3., np.nan]]); lons = np.array([0., 1.]); lats = np.array([0., 1.])
    assert bilinear(grid, lons, lats, np.array([0., 1.]), np.array([0., 0.])).tolist() == [1., 2.]
    assert bilinear(grid, lons, lats, np.array([.5]), np.array([.5]))[0] == pytest.approx(2.)


def test_storm_api_real_scenario():
    client = app.test_client()
    ok = client.post('/api/storm', json={'scenario': 'darragh-2024', 'seed': 2})
    assert ok.status_code == 200 and 'benchmark' in ok.get_json()
    assert client.post('/api/storm', json={'scenario': 'nope'}).status_code == 400
