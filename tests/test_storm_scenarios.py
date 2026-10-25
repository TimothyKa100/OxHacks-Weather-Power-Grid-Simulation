"""Synthetic scenario contract, coherent weather, feature history and async API."""
import json
from threading import Event
from time import monotonic, sleep

import numpy as np
import pytest

from backend.storm_scenarios import DEFAULT_SCENARIO, PRESETS, generate_weather, track, validate_scenario
from backend.scenario_adapter import run_scenario, scenario_context
from backend.scenario_jobs import ScenarioJobs, JobCapacityError
from backend.app import app, nets
from backend.weather import validate_weather


@pytest.mark.parametrize('change', [
    {'version': True}, {'version': 2}, {'hours': 6.5}, {'seed': True}, {'seed': -1},
    {'speed_kmh': 0}, {'radius_km': float('nan')}, {'peak_gust_kmh': float('inf')},
    {'initial_soil_moisture': 1}, {'preset': []}, {'origin': {'lon': -10}},
    {'origin': {'lon': -10, 'lat': 0}}, {'origin': {'lon': True, 'lat': 55}},
    {'start_time': '2026-01-01T00:30:00Z'}, {'start_time': '2026-01-01'},
    {'start_time': None}, {'speed': 30},
])
def test_invalid_scenarios_are_rejected(change):
    with pytest.raises(ValueError):
        validate_scenario(change)


def test_contract_normalization_and_track_units():
    config = validate_scenario({'origin': {'lon': -10, 'lat': 50}, 'bearing_deg': 90, 'speed_kmh': 50})
    assert config['origin'] is not DEFAULT_SCENARIO['origin']
    points = track(config, [0, 1, 2])
    np.testing.assert_allclose(points[:, 1], 50)
    np.testing.assert_allclose(np.diff(points[:, 0]) * 111.32 * np.cos(np.deg2rad(50)), 50)
    assert validate_scenario({'start_time': '2026-01-01T01:00:00+01:00'})['start_time'] == DEFAULT_SCENARIO['start_time']


@pytest.mark.parametrize('preset', PRESETS)
def test_complete_bounded_weather_with_warmup_and_stable_cell_identity(preset):
    config = {'preset': preset, 'hours': 24, 'origin': {'lon': -6, 'lat': 54}, 'seed': 99}
    locations = [[-6, 54], [-3, 55], [-18, 46]]
    result = generate_weather(config, locations)
    assert result['hours'].tolist() == list(range(-72, 25))
    assert len(result['timestamps']) == 97
    for key, values in result['variables'].items():
        assert values.shape == (97, 3)
        assert np.isfinite(values).all()
        np.testing.assert_array_equal(values, generate_weather(config, locations)['variables'][key])
        np.testing.assert_array_equal(values[:, [2, 0]], generate_weather(config, [locations[2], locations[0]])['variables'][key])
    v = result['variables']
    assert np.all(v['wind_speed_10m'] <= v['wind_gusts_10m'])
    assert v['wind_gusts_10m'].max() <= 160
    assert np.all(v['wind_gusts_10m'][:72] == 18)
    assert np.all(v['precipitation'][:73] == 0)  # completed interval before storm arrival
    assert v['precipitation'][73:, 0].max() > 0
    assert np.all((v['soil_moisture_0_to_7cm'] >= .05) & (v['soil_moisture_0_to_7cm'] <= .55))
    assert v['soil_moisture_0_to_7cm'][-1, 0] > v['soil_moisture_0_to_7cm'][72, 0]
    assert np.all(v['wind_direction_10m'] == np.round(v['wind_direction_10m']))


def test_weather_responds_to_location_season_wetness_and_seed():
    location = [[-6, 54]]
    base = {'origin': {'lon': -6, 'lat': 54}}
    first = generate_weather(base, location)['variables']
    away = generate_weather({**base, 'origin': {'lon': -23, 'lat': 46}}, location)['variables']
    assert first['wind_gusts_10m'][72, 0] > away['wind_gusts_10m'][72, 0] + 50
    summer = generate_weather({**base, 'start_time': '2026-07-01T00:00:00Z'}, location)['variables']
    assert summer['temperature_2m'][72, 0] > first['temperature_2m'][72, 0] + 8
    wetter = generate_weather({**base, 'initial_soil_moisture': .45}, location)['variables']
    assert wetter['soil_moisture_0_to_7cm'][72, 0] == .45
    changed = generate_weather({**base, 'seed': 2}, location)['variables']
    assert not np.array_equal(changed['wind_gusts_10m'], first['wind_gusts_10m'])


def test_ml_adapter_reuses_feature_contract_and_preserves_historical_builder():
    pd = pytest.importorskip('pandas')
    pytest.importorskip('pyarrow')
    from failure_model.common import FEATURES, STATIC_FEATURES
    from failure_model.features import FeatureBuilder, crosswind_envelope
    from failure_model.scenarios import scenario_features
    # A real FeatureBuilder's in-memory contract, without modifying/downloads of datasets.
    builder = FeatureBuilder.__new__(FeatureBuilder)
    builder.assets = pd.DataFrame([{'asset_id': 'feeder-a', 'route_weather_complete': False,
                                    **{key: 1. for key in STATIC_FEATURES}}])
    builder.links = pd.DataFrame([{'asset_id': 'feeder-a', 'weather_key': '54.00_-6.00',
                                   'length_fraction': 1., 'bearing_degrees': [0., 90.]}])
    builder.groups = {'feeder-a': builder.links}
    builder.envelopes = {('feeder-a', '54.00_-6.00'): crosswind_envelope([0., 90.])}
    table = scenario_features(builder, {'hours': 6, 'origin': {'lon': -6, 'lat': 54}})
    result = table.to_pandas()
    assert len(result) == 6
    assert result[FEATURES].notna().all().all()
    assert not builder.assets.route_weather_complete.iloc[0]
    assert result.gust_max_prev_72h_kmh.iloc[0] == 18  # warm-up is available at first cutoff
    assert result.gust_route_max_lag_1h_kmh.iloc[0] == 18
    assert result.gust_route_max_lag_1h_kmh.iloc[1] > 50  # correct one-hour lag
    assert result.precipitation_prev_1h_mm.iloc[0] == 0
    assert result.precipitation_prev_1h_mm.iloc[1] > 0
    assert result.overhead_length_km.eq(1).all()
    assert table.schema.metadata[b'mode'] == b'synthetic_scenario'


def test_single_grid_run_is_reproducible_and_raw_weather_stays_server_side():
    net = nets['gb']
    config = {'hours': 6, 'origin': {'lon': -3, 'lat': 54}, 'peak_gust_kmh': 190, 'seed': 23}
    first = run_scenario(net, config)
    assert first == run_scenario(net, config)
    assert len(first['frames']) == 7
    assert first['frames'][0]['storm'] is None
    assert first['frames'][1]['storm'] is not None
    assert len(first['frames'][1]['gust_kmh']) == net.m
    assert first['replay']['request']['storm_config']['origin'] == config['origin']
    assert 'variables' not in first and 'planning_weather' not in first
    assert first['network']['total_load_mw'] == float(net.load.sum())
    context = scenario_context(net, json.dumps(validate_scenario(config), sort_keys=True))
    validate_weather(context['planning_weather'], net.m)
    moved = run_scenario(net, {**config, 'origin': {'lon': -23, 'lat': 46}})
    assert max(first['frames'][1]['gust_kmh']) > max(moved['frames'][1]['gust_kmh'])


def wait_for_job(client, job_id):
    deadline = monotonic() + 30
    while monotonic() < deadline:
        response = client.get(f'/api/storm-scenarios/runs/{job_id}')
        assert response.status_code == 200
        if response.json['status'] in ('complete', 'failed'):
            assert response.json['status'] == 'complete', response.json
            return response.json['result']
        sleep(.01)
    pytest.fail('Storm job timed out')


def test_async_single_run_and_comparison_preserve_scenario_in_replay():
    client = app.test_client()
    config = {'hours': 6, 'peak_gust_kmh': 100, 'seed': 73}
    payload = {'storm_config': config, 'mode': 'single'}
    response = client.post('/api/storm-scenarios/runs', json=payload)
    assert response.status_code == 202
    first = wait_for_job(client, response.json['job_id'])
    assert first['config']['hours'] == 6 and 'plan' not in first
    assert client.post('/api/storm-scenarios/runs', json=payload).json['job_id'] == response.json['job_id']
    response = client.post('/api/storm-scenarios/runs', json={**payload, 'mode': 'compare', 'budget': 0})
    compared = wait_for_job(client, response.json['job_id'])
    assert compared['replay']['request']['storm_config'] == first['replay']['request']['storm_config']
    assert compared['frames'][1]['storm'] == first['frames'][1]['storm']
    assert compared['plan']['config']['network'] == 'gb'
    assert compared['config']['seed'] == 73
    replay = client.post('/api/storm-plan', json=compared['replay']['request'])
    assert replay.status_code == 200
    assert replay.json['frames'] == compared['frames']


@pytest.mark.parametrize('payload', [None, [], {}, {'mode': 'wrong'}, {'storm_config': {} , 'crews': True},
    {'storm_config': {}, 'unexpected': 1}, {'mode': 'compare', 'scenario': 'imported', 'storm_config': {}},
    {'mode': 'single', 'storm_config': {'hours': 1000}},
])
def test_job_api_rejects_invalid_inputs_before_queuing(payload):
    assert app.test_client().post('/api/storm-scenarios/runs', json=payload).status_code == 400


def test_contract_discovery_and_expired_job():
    client = app.test_client()
    assert client.get('/api/storm-scenarios').json['defaults'] == DEFAULT_SCENARIO
    assert client.get('/api/storm-scenarios/runs/nonexistent').status_code == 404


def test_scenario_contract_is_authoritative_over_legacy_aliases():
    from backend.app import validate_storm_plan
    config=validate_scenario({'hours':6,'peak_gust_kmh':120.5})
    params=validate_storm_plan({'storm_config':config,'hours':36,'seed':99,'peak_gust':160})
    assert params['hours']==6 and params['seed']==1 and params['peak_gust']==120.5
    replay=validate_storm_plan(params)
    assert replay==params


def test_job_queue_bounds_deduplication_expiry_and_failure():
    jobs = ScenarioJobs(max_pending=1, ttl_seconds=600)
    ready, release = Event(), Event()
    def blocking():
        ready.set()
        release.wait(5)
        return {'ok': True}
    try:
        key = jobs.submit(blocking, cache_key='identical')
        assert ready.wait(2)
        assert jobs.submit(blocking, cache_key='identical') == key
        with pytest.raises(JobCapacityError): jobs.submit(lambda: None)
        release.set()
        deadline = monotonic() + 3
        while jobs.get(key)['status'] != 'complete' and monotonic() < deadline: sleep(.01)
        assert jobs.get(key)['result'] == {'ok': True}
        jobs.ttl = -1
        assert jobs.get(key) is None
        jobs.ttl = 600
        failed = jobs.submit(lambda: 1 / 0)
        deadline = monotonic() + 3
        while jobs.get(failed)['status'] != 'failed' and monotonic() < deadline: sleep(.01)
        assert 'ZeroDivisionError' not in jobs.get(failed)['error']
    finally:
        release.set()
        jobs.executor.shutdown(wait=True)
