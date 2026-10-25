import copy
import numpy as np
import pytest
from backend.hybrid import combine,validate_pair,hybrid_weather,settings
from backend.simulation import Network,simulate
from backend.weather import multiday_scenarios
from backend.app import app,nets


def fixtures(ids,hours=2,p=.05):
    dates=[f'2026-01-01T0{i}:00:00Z' for i in range(hours)]
    weather={'source':'Synthetic physics fixture','branch_ids':ids,'engineering_model':{'name':'route-pylon-v1'},
        'hours':[dict(timestamp=t,gust_kmh=[30]*len(ids),rain_mm_h=0,failure_probability=[p]*len(ids)) for t in dates]}
    pair={'source':'Synthetic paired predictions, not an empirical model','prediction_unit':'paired_risk_modifier','branch_ids':ids,
        'prediction':dict(model_version='full-fixture',reference_model_version='wind-fixture',target='transmission_weather_failure',
                          evidence_status='synthetic_fixture',training_cutoff='2025-01-01T00:00:00Z',issued_at=dates[0]),
        'hours':[dict(timestamp=t,full_probability=[.2]*len(ids),reference_probability=[.1]*len(ids)) for t in dates]}
    return weather,pair


def test_hazard_formula_endpoints_and_limits():
    p,m=combine(.2,.2,.1)
    assert m==pytest.approx(1.5) and p==pytest.approx(1-.8**1.5)
    assert combine(.2,.2,.1,{'beta':0})[0]==pytest.approx(.2)
    assert combine(.2,.2,.2)[0]==pytest.approx(.2)
    p,m=combine([0,1,.2],[1,1,1],[0,0,0])
    assert np.isfinite(p).all() and p[0]==0 and p[1]==1 and np.all(m<=2)
    assert combine(.2,0,1)[1]==pytest.approx(.5)
    for value in [float('nan'),-1,2]:
        with pytest.raises(ValueError): combine(value,.2,.1)


def test_pair_alignment_and_provenance():
    _,pair=fixtures(['A'])
    validate_pair(pair,['A'])
    for mutate in [lambda p:p['hours'][0].update(full_probability=[float('nan')]),
                   lambda p:p['prediction'].update(target='customer_count'),
                   lambda p:p['prediction'].pop('reference_model_version'),
                   lambda p:p['prediction'].update(issued_at='2026-01-02T00:00:00Z')]:
        broken=copy.deepcopy(pair);mutate(broken)
        with pytest.raises(ValueError):validate_pair(broken,['A'])
    with pytest.raises(ValueError):validate_pair(pair,['B'])
    with pytest.raises(ValueError):validate_pair(pair,['A'],['2026-02-01T00:00:00Z'])


def test_real_sampler_uses_combined_probability_and_hardens_afterwards():
    net=Network(); ids=[e['id'] for e in net.branches]
    weather,pair=fixtures(ids,p=.2)
    s=multiday_scenarios(net,100,26,1,weather=hybrid_weather(weather,pair))[0]
    s['draws'][:]=1; s['draws'][0,0]=.1; s['draws'][0,1]=.25
    run=simulate(net,s,[0],True)
    f=run['frames'][1];d=f['probability_diagnostics']
    final=1-.8**1.5
    assert d['unreinforced_probability'][0]==pytest.approx(final)
    assert d['applied_probability'][0]==pytest.approx(1-(1-final)**.25)
    assert not d['sampled_weather_failure'][0] and d['sampled_weather_failure'][1]
    assert f['status'][1]=='weather'
    assert d['uniform_draw'][1]==.25


def test_api_comparison_beta_zero_and_rerun():
    ids=[e['id'] for e in nets['gb'].branches]
    weather,pair=fixtures(ids)
    payload=dict(scenario='imported',weather=weather,failure_mode='hybrid',hybrid_profile=pair,
                 hybrid={'beta':0},samples=16,budget=1,seed=26)
    response=app.test_client().post('/api/storm-plan',json=payload)
    assert response.status_code==200,response.json
    result=response.json
    assert result['hazard_comparison']['none']['engineering']==result['hazard_comparison']['none']['hybrid']
    assert result['hybrid_provenance']['prediction']['evidence_status']=='synthetic_fixture'
    second=app.test_client().post('/api/storm-plan',json={**result['replay']['request'],'custom_lines':[]})
    assert second.status_code==200,second.json
    assert result['plan']['portfolios']['none']['metrics']==second.json['plan']['portfolios']['custom']['metrics']
    assert second.json['replay']['request']['hybrid_profile']==pair
    bad=copy.deepcopy(payload);bad['hybrid_profile']['branch_ids'].reverse()
    assert app.test_client().post('/api/storm-plan',json=bad).status_code==400
    assert app.test_client().post('/api/storm-plan',json={**payload,'hybrid_profile':None}).status_code==400


def test_fragility_sensitivity_does_not_change_weather():
    from backend.storm import run_storm
    net=nets['gb']
    a=run_storm(net,190,6,7,median_gust=250)
    b=run_storm(net,190,6,7,median_gust=300)
    assert [r['gust_kmh'] for r in a['planning_weather']['hours']]==[r['gust_kmh'] for r in b['planning_weather']['hours']]
    assert np.max([r['failure_probability'] for r in a['planning_weather']['hours']])>np.max([r['failure_probability'] for r in b['planning_weather']['hours']])
