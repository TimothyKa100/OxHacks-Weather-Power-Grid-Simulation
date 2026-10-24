import copy
import numpy as np
import pytest
from backend.app import app
from backend.decisions import cost_benefit, validate_economics
from backend.simulation import Network, simulate
from backend.weather import multiday_scenarios, validate_weather
from scripts.evaluate_predictions import score


def profile(probability=None):
    result = {'source':'Artificial contract fixture, not observed weather','hours':[
        {'timestamp':f'2026-01-01T0{h}:00:00Z','gust_kmh':40,'rain_mm_h':0} for h in range(4)]}
    if probability is not None:
        result['branch_ids']=[f'L{i:02d}' for i in range(1,21)]
        result['prediction']={'model_version':'test-only','training_cutoff':'2025-01-01T00:00:00Z',
                              'issued_at':'2025-12-31T00:00:00Z'}
        for hour in result['hours']: hour['failure_probability']=[probability]*20
    return result


def test_economics_negative_zero_and_break_even():
    assumptions=validate_economics(dict(cost_per_unit_gbp=100,outage_value_gbp_per_mwh=10,expected_events=3))
    result=cost_benefit(20,2,assumptions)
    assert result['investment_gbp']==200
    assert result['net_benefit_gbp']==400
    assert result['benefit_cost_ratio']==3
    assert result['break_even_events']==1
    assert cost_benefit(-20,2,assumptions)['net_benefit_gbp']==-800
    assert cost_benefit(-20,2,assumptions)['break_even_events'] is None
    assert cost_benefit(20,0,assumptions)['benefit_cost_ratio'] is None
    assert cost_benefit(20,2,assumptions,investment_gbp=50)['benefit_cost_ratio']==12
    for invalid in [True,float('nan'),-1,'100']:
        with pytest.raises(ValueError): validate_economics({'cost_per_unit_gbp':invalid})


def test_presets_differ_but_keep_counterfactual_random_draws():
    net=Network()
    scenarios=[multiday_scenarios(net,100,26,1,48,storm_type=p)[0]
               for p in ('single_peak','persistent','double_peak')]
    assert not np.array_equal(scenarios[0]['gust'],scenarios[1]['gust'])
    assert not np.array_equal(scenarios[0]['gust'],scenarios[2]['gust'])
    assert np.array_equal(scenarios[0]['draws'],scenarios[2]['draws'])
    assert np.array_equal(scenarios[0]['repair_hours'],scenarios[2]['repair_hours'])


def test_custom_plan_equals_suggested_and_is_reproducible():
    client=app.test_client()
    request=dict(mode='multiday',hours=12,samples=16,budget=3)
    first=client.post('/api/experiment',json=request)
    assert first.status_code==200
    first=first.json
    selected=first['portfolios']['optimized']['selected']
    second=client.post('/api/experiment',json={**first['replay']['request'],'custom_lines':selected})
    assert second.status_code==200
    second=second.json
    assert second['portfolios']['custom']==second['portfolios']['optimized']
    assert first['portfolios']['none']==second['portfolios']['none']
    assert first['demo_scenario_index']==second['demo_scenario_index']
    assert set(second['decisions']['repair_priority'])=={f'L{i:02d}' for i in range(1,21)}
    assert len([a for a in second['decisions']['assets'] if a['kind']=='transformer'])==5
    assert second['portfolios']['prepared']['spent']==0
    empty=client.post('/api/experiment',json={**request,'custom_lines':[]}).json
    assert empty['portfolios']['custom']==empty['portfolios']['none']


@pytest.mark.parametrize('extra',[
    {'custom_lines':['L99']}, {'custom_lines':['L01','L01']}, {'custom_lines':'L01'},
    {'custom_lines':['L03'],'budget':0}, {'storm_type':'flood'},
    {'economics':{'expected_events':float('nan')}}, {'economics':[]},
    {'economics':{'asset_costs_gbp':{'L99':100}}},
    {'economics':{'asset_costs_gbp':{'L01':-1}}},
])
def test_bad_decision_inputs(extra):
    assert app.test_client().post('/api/experiment',json={**dict(samples=16),**extra}).status_code==400


def test_imported_model_probabilities_override_fragility_and_validate_provenance():
    net=Network()
    p=profile(0.)
    scenario=multiday_scenarios(net,140,26,1,weather=p)[0]
    scenario['draws'][:]=0.01
    assert simulate(net,scenario)['failed_lines']==0
    scenario['failure_probability'][:]=1.
    assert simulate(net,scenario)['failed_lines']==20
    broken=copy.deepcopy(p); broken.pop('prediction')
    with pytest.raises(ValueError): validate_weather(broken,20)
    broken=copy.deepcopy(p); broken['prediction']['issued_at']='2026-01-01T01:00:00Z'
    with pytest.raises(ValueError): validate_weather(broken,20)
    broken=copy.deepcopy(p); broken['hours'][1].pop('failure_probability')
    with pytest.raises(ValueError): validate_weather(broken,20)
    broken=copy.deepcopy(p); broken['branch_ids'].reverse()
    response=app.test_client().post('/api/experiment',json=dict(mode='multiday',samples=16,weather=broken))
    assert response.status_code==400
    # Hardening is an assumed hazard reduction, not a second application of fragility.
    scenario=multiday_scenarios(net,140,26,1,weather=profile(.5))[0]
    scenario['draws'][:]=1
    frame=simulate(net,scenario,[0],True)['frames'][1]
    assert frame['failure_probability'][0]==pytest.approx(1-.5**.25,abs=1e-6)
    assert frame['failure_probability'][1]==.5


def test_priority_queue_changes_repair_order_without_mutating_original():
    net=Network()
    s=multiday_scenarios(net,30,26,1,hours=5,crews=1)[0]
    s['gust'][:]=30; s['draws'][:]=1; s['draws'][0,:]=0; s['repair_hours'][:]=1
    first=simulate(net,s,playback=True)
    reverse=simulate(net,dict(s,repair_priority=list(reversed(range(20)))),playback=True)
    f=next(f for f in first['frames'] if f['hour']==2)
    r=next(f for f in reverse['frames'] if f['hour']==2)
    assert f['status'][0]=='online' and r['status'][-1]=='online'
    assert simulate(net,s,playback=True)==first
    assert first['recovery_hour'] is None  # incomplete recovery is censored


def test_real_prediction_evaluator_checks_storm_separation():
    manifest=dict(model_version='test',training_cutoff='2025-01-01T00:00:00Z',training_storms=['train'],
                  baseline_failure_probability=.5,labels_source='Fixture only')
    rows=[dict(storm_id='test',timestamp='2026-01-01T00:00:00Z',asset_id=f'A{i}',
               probability=p,failed=str(y)) for i,(p,y) in enumerate([(.1,0),(.9,1)])]
    result=score(rows,manifest)
    assert result['brier_score']==pytest.approx(.01)
    assert result['brier_skill']==pytest.approx(.96)
    assert sum(b['count'] for b in result['calibration'])==2
    with pytest.raises(ValueError): score(rows,{**manifest,'training_storms':['test']})
    with pytest.raises(ValueError): score(rows+rows,manifest)
