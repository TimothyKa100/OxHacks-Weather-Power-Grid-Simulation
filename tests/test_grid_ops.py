"""GB storm-time operation: failure types, protection, re-dispatch, islands, generation and demand."""
import numpy as np
import pytest

from backend.grid_ops import (PARAMS, demand_factor, secure_dispatch, simulate_gb, solve, transient_probability,
                              wind_power_fraction)
from backend.simulation import Network
from backend.weather import multiday_scenarios


@pytest.fixture(scope='module')
def gb(): return Network('gb')


def calm(gb, hours=4):
    """Calm replay with every random draw set so nothing fails unless a test says so."""
    profile = {'source': 'Calm test fixture', 'hours': [
        {'timestamp': f'2026-01-01T{12+h:02d}:00:00Z', 'gust_kmh': 20, 'rain_mm_h': 0} for h in range(hours)]}
    s = multiday_scenarios(gb, 30, 1, 1, hours=hours, weather=profile, crews=6)[0]
    s['gust'] = s['gust'].copy()
    for key in ('draws', 'transient_draws', 'reclose_draws'): s[key][:] = 1
    s['common_cause'][:] = 1
    s['failure_probability'] = np.zeros((hours, gb.m))
    return s


def overhead_double_circuit(gb):
    return next(i for i, e in enumerate(gb.branches) if e['kind'] == 'line' and e['circuits'] == 2)


def test_calm_operation_is_secure_and_sheds_nothing(gb):
    run = simulate_gb(gb, calm(gb), playback=True)
    assert run['unserved_mwh'] == pytest.approx(0, abs=1e-6) and run['cascade_trips'] == 0
    for frame in run['frames']:
        assert max(frame['loading']) <= PARAMS['short_term_rating']+1e-6


def test_failed_autoreclose_locks_out_one_circuit_until_remote_reclose(gb):
    i = overhead_double_circuit(gb)
    s = calm(gb)
    s['gust'][0, i] = 140.  # storm gust makes a transient fault likely on this route
    s['transient_draws'][0, i, 0] = 0.; s['reclose_draws'][0, i, 0] = 1.  # fault, auto-reclose fails
    run = simulate_gb(gb, s, playback=True)
    settled = {f['hour']: f for f in run['frames'] if f['kind'] == 'Settled'}
    assert settled[1]['circuits_in_service'][i] == 1 and settled[1]['status'][i] == 'online'
    assert settled[2]['circuits_in_service'][i] == 2  # remote reclose next hour, no crew
    assert run['lockouts'] == 1 and run['repairs'] == 0


def test_successful_autoreclose_leaves_no_outage(gb):
    i = overhead_double_circuit(gb)
    s = calm(gb)
    s['gust'][0, i] = 140.
    s['transient_draws'][0, i, 0] = 0.; s['reclose_draws'][0, i, 0] = 0.
    run = simulate_gb(gb, s, playback=True)
    assert run['transient_faults'] == 1 and run['reclosed_transients'] == 1 and run['lockouts'] == 0
    assert all(f['circuits_in_service'][i] == 2 for f in run['frames'])


def test_tower_damage_takes_every_circuit_and_needs_a_crew_in_safe_weather(gb):
    i = overhead_double_circuit(gb)
    s = calm(gb, hours=5)
    s['failure_probability'][0, i] = 1.; s['draws'][0, i] = 0.; s['repair_hours'][0, i] = 2.
    run = simulate_gb(gb, s, playback=True)
    settled = {f['hour']: f for f in run['frames'] if f['kind'] == 'Settled'}
    assert settled[1]['circuits_in_service'][i] == 0 and settled[1]['status'][i] == 'weather'
    assert settled[3]['status'][i] == 'online' and run['repairs'] == 1
    windy = calm(gb, hours=5)
    windy['failure_probability'][0, i] = 1.; windy['draws'][0, i] = 0.; windy['repair_hours'][0, i] = 2.
    windy['gust'][:, i] = 80.  # unsafe for crews on this route
    assert simulate_gb(gb, windy)['repairs'] == 0


def test_damage_repairs_take_days():
    from backend.grid_ops import damage_repair_hours
    hours = damage_repair_hours(np.random.default_rng(1), 20000)
    low, high = PARAMS['damage_repair_bounds_h']
    assert low <= hours.min() and hours.max() <= high and 72 < np.median(hours) < 120


def test_island_needs_synchronous_capacity(gb):
    variable, firm, sync = gb.grid_model.availability(np.zeros(gb.m), '2026-01-01T12:00:00Z')
    loaded = [b for b in range(gb.n) if gb.load[b] > 1 and sync[b] == 0 and variable[b] == 0]
    boundary = [b for b in range(gb.n) if gb.buses[b].get('boundary') and gb.load[b] > 1]
    fraction = np.ones(gb.m)
    for b in (loaded[0], boundary[0]):
        fraction[(gb.ends[:, 0] == b) | (gb.ends[:, 1] == b)] = 0.
    s = solve(gb, fraction, gb.load, variable, firm, sync)
    assert s['served'][loaded[0]] == 0  # no synchronous supply: the island collapses
    # A boundary supply (half its demand, synchronous) holds its island at half load.
    assert s['served'][boundary[0]] == pytest.approx(min(1., (firm[boundary[0]]+variable[boundary[0]])/gb.load[boundary[0]])*gb.load[boundary[0]])


def test_preventive_redispatch_relieves_overloads(gb, monkeypatch):
    # Derate the network so the windy evening peak overloads lines, then let operators re-dispatch.
    monkeypatch.setitem(PARAMS, 'rating_scale', .6)
    stamp = '2026-01-01T18:00:00Z'
    demand = gb.load*demand_factor([stamp], gb.load.sum())[0]
    variable, firm, sync = gb.grid_model.availability(np.full(gb.m, 50.), stamp)
    full = np.ones(gb.m); limits = gb.ratings*.6
    before = np.abs(solve(gb, full, demand, variable, firm, sync)['flows'])/limits
    adjust = secure_dispatch(gb, full, demand, variable, firm, sync)
    after = np.abs(solve(gb, full, demand, variable, firm, sync, adjust)['flows'])/limits
    assert before.max() > 1 and after.max() < before.max()-.05
    assert (after > PARAMS['preventive_margin']+1e-6).sum() < (before > PARAMS['preventive_margin']+1e-6).sum()


def test_wind_cuts_out_in_storms_and_demand_peaks_in_the_evening():
    assert wind_power_fraction(0) == 0 and wind_power_fraction(60) == pytest.approx(1)
    assert wind_power_fraction(130) == 0  # hub wind above cut-out
    f = demand_factor(['2026-01-01T04:00:00Z', '2026-01-01T18:00:00Z'], 30000.)
    assert f[1] > 1.4 and f[0] < 1


def test_hardening_raises_fragility_and_halves_transient_rate(gb):
    i = overhead_double_circuit(gb)
    s = calm(gb, hours=2)
    s['failure_probability'][:] = .5
    s['failure_probability_hardened'] = np.full((2, gb.m), .1)
    frame = simulate_gb(gb, s, [i], playback=True)['frames'][-1]
    assert frame['failure_probability'][i] == pytest.approx(.1) and frame['failure_probability'][i+1 if i+1 < gb.m else 0] == .5
    gust = np.full(gb.m, 120.)
    plain = transient_probability(gb.grid_model, gust, np.zeros(gb.m, bool))
    hard = transient_probability(gb.grid_model, gust, np.ones(gb.m, bool))
    assert hard[i] == pytest.approx(-np.expm1(np.log1p(-plain[i])*PARAMS['hardening_transient_factor']))
    assert all(plain[j] == 0 for j, e in enumerate(gb.branches) if e['kind'] != 'line')  # cables/transformers


def test_engineering_hardened_probabilities_use_a_higher_fragility_median(gb):
    from backend.storm import run_storm
    hours = run_storm(gb, 190, 6, 3)['planning_weather']['hours']
    base = np.array([h['failure_probability'] for h in hours]); hard = np.array([h['failure_probability_hardened'] for h in hours])
    assert np.all(hard <= base+1e-12) and (hard < base).any()
