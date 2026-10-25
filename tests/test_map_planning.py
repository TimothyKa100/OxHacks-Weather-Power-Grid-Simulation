"""The geographic UI must display the same decisions evaluated by the planner."""
import copy
import numpy as np
import pytest
from backend.app import app, nets, cached_storm_plan
from backend.planning import map_frames
from backend.weather import validate_weather


def fixture_weather():
    net=nets['gb']
    return {'source':'Artificial integration fixture; not observed weather',
            'hours':[{'timestamp':f'2026-01-01T0{t}:00:00Z','gust_kmh':30,'rain_mm_h':0} for t in range(4)]}


def test_map_plan_reruns_recommendations_on_same_gb_storm():
    client=app.test_client()
    first=client.post('/api/storm-plan',json=dict(hours=6,samples=16,budget=2,peak_gust=190,seed=7,recovery_hours=0,failure_mode='engineering'))
    assert first.status_code==200
    first=first.json
    assert first['plan']['config']['network']=='gb'
    assert first['plan']['config']['mode']=='multiday'
    assert len(first['frames'])==7
    assert set(first['frames'][-1]['status'])<= {'online','weather','overload'}
    assert first['plan']['weather']['engineering_model']['name']=='route-pylon-v1'
    assert first['replay']['request']['weather'] is None  # internal GB fields aren't sent back as a huge request
    suggested=first['plan']['portfolios']['optimized']['selected']
    second=client.post('/api/storm-plan',json={**first['replay']['request'],'custom_lines':suggested})
    assert second.status_code==200
    second=second.json
    assert second['plan']['portfolios']['custom']==second['plan']['portfolios']['optimized']
    assert first['frames']==second['frames']
    assert first['plan']['demo_scenario_index']==second['plan']['demo_scenario_index']
    assert first['plan']['portfolios']['none']==second['plan']['portfolios']['none']
    # Repeated identical requests hit the full validated-request cache.
    before=cached_storm_plan.cache_info().hits
    assert client.post('/api/storm-plan',json=second['replay']['request']).status_code==200
    assert cached_storm_plan.cache_info().hits==before+1


def test_imported_weather_uses_same_engine_and_has_no_fake_storm_path():
    client=app.test_client()
    request=dict(scenario='imported',weather=fixture_weather(),samples=16,budget=0,recovery_hours=0)
    first=client.post('/api/storm-plan',json=request)
    assert first.status_code==200
    first=first.json
    assert len(first['frames'])==5
    assert all(f['storm'] is None for f in first['frames'])
    assert first['input_source']==request['weather']['source']
    second=client.post('/api/storm-plan',json={**first['replay']['request'],'custom_lines':[]})
    assert second.status_code==200
    assert second.json['plan']['portfolios']['custom']==first['plan']['portfolios']['none']


@pytest.mark.parametrize('change',[
    {'scenario':'unknown'}, {'scenario':[]}, {'scenario':'imported'}, {'seed':True},
    {'custom_lines':['L9999']}, {'custom_lines':['L001','L001']}, {'custom_lines':['L001'],'budget':0},
    {'samples':0}, {'crews':0}, {'storm_type':'unknown'},
    {'economics':{'asset_costs_gbp':{'unknown':500}}},
    {'scenario':'synthetic','weather':fixture_weather()},
])
def test_bad_map_plans_are_rejected_before_simulation(change):
    assert app.test_client().post('/api/storm-plan',json=change).status_code==400


def test_map_adapter_keeps_last_cascade_state_and_repairs():
    def frame(hour,status):
        return dict(hour=hour,status=[status],failure_probability=[.1],islands=[[1]],new_trips=[],served_by_bus=[1])
    playback={'frames':[frame(0,'online'),frame(1,'online'),frame(1,'weather'),frame(2,'online')]}
    context={'network':{'branches':[{'id':'A'}]},'frames':[{'hour':1,'storm':[.2,.3],'storm_radius':.1}]}
    frames=map_frames(playback,context)
    assert len(frames)==3 and frames[1]['status']==['weather'] and frames[2]['status']==['online']
    assert frames[1]['storm']==[.2,.3]
    assert frames[2]['events']==[{'type':'online','line':'A'}]
    assert all(f['fallen_at']==[None] for f in frames)


def test_engineering_provenance_does_not_relax_ml_provenance():
    profile=fixture_weather()
    profile['branch_ids']=[e['id'] for e in nets['gb'].branches]
    for row in profile['hours']: row['failure_probability']=[.01]*nets['gb'].m
    with pytest.raises(ValueError): validate_weather(profile,nets['gb'].m)
    profile['engineering_model']={'name':'route-pylon-v1'}
    validate_weather(profile,nets['gb'].m)
    profile['prediction']={'model_version':'other'}
    with pytest.raises(ValueError): validate_weather(profile,nets['gb'].m)
