import copy
import numpy as np
import pytest
from backend.weather import failure_probability, multiday_scenarios, validate_weather
from backend.simulation import Network, simulate
from backend.app import app


def test_hazard_time_consistency():
    p = failure_probability(100, 10, 1., 1.)
    half = failure_probability(100, 10, 1., .5)
    assert 1-(1-half)**2 == pytest.approx(p)
    assert failure_probability(100, 10, 1., 1., True) < p


def test_repairs_restore_supply_and_integrate_energy():
    net = Network()
    s = multiday_scenarios(net, 30, 26, 1, hours=24, crews=10)[0]
    s['draws'][:] = 1
    s['draws'][0, :] = 0  # initial physical failures, then calm/no further failures
    s['repair_hours'][:] = 2
    run = simulate(net, s, playback=True)
    settled = [next(f for f in reversed(run['frames']) if f['hour']==h) for h in range(1,25)]
    assert run['final_shed_mw'] == pytest.approx(0, abs=1e-6)
    assert run['repairs'] >= net.m
    assert run['failed_lines'] == net.m
    assert run['unserved_mwh'] == pytest.approx(sum(f['shed_mw'] for f in settled), abs=.03)
    assert simulate(net, s)['unserved_mwh'] == run['unserved_mwh']


def test_unsafe_weather_pauses_repair():
    net = Network()
    s = multiday_scenarios(net, 30, 26, 1, hours=4)[0]
    s['gust'][:] = 80
    s['draws'][:] = 1; s['draws'][0,:] = 0
    s['repair_hours'][:] = 1
    run = simulate(net,s)
    assert run['repairs'] == 0
    assert run['final_shed_mw'] > 0


def test_import_validation_and_api_replay():
    profile = {'source':'Test fixture, not observed weather', 'hours':[
        {'timestamp':f'2026-01-01T0{h}:00:00Z','gust_kmh':[30]*20,'rain_mm_h':0} for h in range(3)]}
    validate_weather(profile,20)
    broken = copy.deepcopy(profile); broken['hours'][1]['timestamp']=broken['hours'][0]['timestamp']
    with pytest.raises(ValueError): validate_weather(broken,20)
    for value in [float('nan'), -1, [30]*19, True, None]:
        broken=copy.deepcopy(profile); broken['hours'][0]['gust_kmh']=value
        with pytest.raises(ValueError): validate_weather(broken,20)
    client=app.test_client()
    payload=dict(mode='multiday',samples=16,budget=0,weather=profile)
    a=client.post('/api/experiment',json=payload)
    assert a.status_code==200
    b=client.post('/api/experiment',json=payload)
    assert a.json['portfolios']==b.json['portfolios']
    assert a.json['config']['hours']==3
    assert a.json['weather']['source']==profile['source']
    assert a.json['portfolios']['none']['playback']['frames'][-1]['timestamp']==profile['hours'][-1]['timestamp']
    assert client.post('/api/experiment',json={**payload,'crews':0}).status_code==400


def test_csv_converter_requires_complete_routes(tmp_path):
    from scripts.import_weather import convert
    path=tmp_path/'weather.csv'
    header='timestamp,line_id,gust_kmh,rain_mm_h\n'
    rows=[f'2026-01-01T00:00:00Z,L{i:02d},80,10\n' for i in range(1,21)]
    path.write_text(header+''.join(rows))
    result=convert(path,'Fixture')
    assert result['hours'][0]['gust_kmh']==[80]*20
    path.write_text(header+''.join(rows[:-1]))
    with pytest.raises(ValueError): convert(path,'Fixture')
    path.write_text(header+''.join(rows)+rows[0])
    with pytest.raises(ValueError): convert(path,'Fixture')
