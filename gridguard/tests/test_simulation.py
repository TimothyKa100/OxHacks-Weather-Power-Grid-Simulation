import numpy as np
import pytest
from backend.simulation import Network, simulate, run_experiment, select_reinforcement
from backend.weather import fragility, scenarios
from backend.app import app

@pytest.fixture(scope='module')
def net(): return Network()

def test_benchmark_flow_matches_pandapower_dc(net):
    solved=net.solve(tuple([True]*net.m),tuple(net.load))
    assert net.n==14 and net.m==20
    assert net.load.sum()==pytest.approx(259)
    assert net.capacity[0]==pytest.approx(332.4)
    assert solved['served'].sum()==pytest.approx(259)
    assert np.allclose(solved['flows'],net.data['reference_flows_mw'],atol=1e-7)
    assert solved['residual_mw']<1e-7

def test_islands_blackout_and_insufficient_generation(net):
    # No branches: bus 2 alone can serve its own load; every other load loses supply.
    isolated=net.solve(tuple([False]*net.m),tuple(net.load))
    assert isolated['served'][1]==pytest.approx(21.7)
    assert isolated['served'].sum()==pytest.approx(21.7)
    assert len(isolated['islands'])==14
    # Isolate bus 1, leaving the other connected buses with only 140 MW available.
    active=tuple(e['from']!=1 and e['to']!=1 for e in net.branches)
    solution=net.solve(active,tuple(net.load))
    assert solution['served'].sum()==pytest.approx(140)
    assert solution['generation'].sum()==pytest.approx(140)

def test_fragility_monotonic_and_hardening():
    p=fragility(np.array([30,60,90,120,140]),15,1.)
    assert np.all(np.diff(p)>0)
    assert np.allclose(fragility(np.array([30,60,90,120,140]),15,1.,True),p*.25)

def test_no_failure_preserves_load_and_cascade_terminates(net):
    s=scenarios(net,100,26,1)[0]
    s['draws'][:]=1
    intact=simulate(net,s,playback=True)
    assert intact['unserved_mwh']==pytest.approx(0,abs=1e-6)
    assert intact['failed_lines']==0
    # Force one branch at a time until an overload trip occurs: demonstrates physics,
    # rather than merely drawing removed edges on a graph.
    cascade=None
    for i in range(net.m):
        s['draws'][:]=1; s['draws'][0,i]=0
        run=simulate(net,s,playback=True)
        if run['cascade_trips']:
            cascade=run; break
    assert cascade is not None
    assert any(f['kind']=='Cascade trip' for f in cascade['frames'])
    assert np.max(cascade['frames'][-1]['loading'])<=1+1e-7
    assert len(cascade['frames'])<=6+net.m
    assert all(a['served_mw']>=b['served_mw']-1e-5 for a,b in zip(cascade['frames'],cascade['frames'][1:]))
    energy=sum(next(f['shed_mw'] for f in reversed(cascade['frames']) if f['hour']==h) for h in range(1,6))
    assert cascade['unserved_mwh']==pytest.approx(energy,abs=.01)

def test_reproducibility_budget_holdout_and_finite_metrics(net):
    a=run_experiment(net,100,26,16,4)
    b=run_experiment(net,100,26,16,4)
    assert a['portfolios']==b['portfolios']
    assert a['config']['training_samples']==24
    for p in a['portfolios'].values():
        assert p['spent']<=4
        assert len(p['selected'])==len(set(p['selected']))
        assert p['metrics']['mean_unserved_mwh']>=0
        assert 0<=p['metrics']['blackout_probability']<=1
    zero=select_reinforcement(net,scenarios(net,100,1,4),0)
    assert zero==([],[])
    assert a['portfolios']['none']['spent']==0
    assert not np.array_equal(scenarios(net,100,26,1)[0]['gust'],scenarios(net,100,27,1)[0]['gust'])

@pytest.mark.parametrize('payload',[None,[],{'samples':0},{'seed':-1},{'budget':13},{'intensity':float('nan')},{'seed':True},{'budget':2.5}])
def test_api_rejects_invalid_input(payload):
    client=app.test_client()
    response=client.post('/api/experiment',json=payload) if payload is not None else client.post('/api/experiment',data='broken',content_type='application/json')
    assert response.status_code==400
    assert 'error' in response.get_json()

def test_api_health_and_zero_budget(net):
    client=app.test_client()
    assert client.get('/api/health').get_json()['status']=='ok'
    response=client.post('/api/experiment',json={'samples':16,'budget':0,'intensity':30})
    assert response.status_code==200
    body=response.get_json()
    assert body['network']['total_load_mw']==pytest.approx(259)
    assert all(not p['selected'] for p in body['portfolios'].values())
    assert len(body['portfolios']['none']['playback']['frames'])>=6
