"""No-new-data hybrid: real inference, conservative transfer, actual supply loss."""
import numpy as np
import pytest
from backend.app import app, nets
from backend.hybrid import redistribute, hybrid_weather
from backend.ml_risk import builtin_pair, matched_reference, saved_model
from backend.simulation import Network, simulate
from backend.storm import run_storm


def test_bounded_tilt_conserves_expected_failures_and_neutral_fallback():
    p=np.array([0,1,1e-25,.001,.02,.2,.9])
    q=np.array([.99,.01,.001,.2,.05,.02,.7])
    reference=np.full(len(p),.02)
    support=np.array([True,True,False,True,True,True,True])
    final,m=redistribute(p,q,reference,support)
    assert final.sum()==pytest.approx(p.sum(),abs=1e-14)
    assert final[0]==0 and final[1]==1 and final[2]==p[2]
    assert np.all((m>=.5)&(m<=2))
    assert np.any(np.abs(final-p)>1e-5)
    assert np.array_equal(redistribute(p,q,reference,support,{'beta':0})[0],p)
    assert np.array_equal(redistribute(p,q,reference,np.zeros(len(p),bool))[0],p)


def test_peer_reference_respects_candidate_mask_and_support():
    q=np.linspace(.01,.4,10); p=np.full(10,.05); gust=np.full(10,100.)
    domain=np.array([True]*9+[False])
    reference,support=matched_reference(q,gust,p,domain)
    assert support[0]==8 and support[-1]==0 and reference[-1]==q[-1]
    from scipy.special import expit,logit
    assert reference[0]==pytest.approx(expit(np.median(logit(q[1:9]))))
    # Isolated weather/hazard exposure is not compared with unlike neighbours.
    gust[0]=200; p[1]=.99
    reference,support=matched_reference(q,gust,p,domain)
    assert support[0]==support[1]==0
    assert reference[0]==q[0] and reference[1]==q[1]


def test_saved_inference_uses_best_iteration_and_records_unknown_weather():
    from xgboost import XGBClassifier
    from scipy.special import expit
    from backend.ml_risk import MODEL_DIR,WEATHER_FEATURES
    net=nets['gb']; exposure=run_storm(net,190,6,7)['planning_weather']
    pair=builtin_pair(net,exposure)
    assert pair['prediction']['feature_coverage']['mode']=='partial_feature_inference'
    assert 'temperature_c' in pair['prediction']['feature_coverage']['missing_features']
    _,calibration,manifest=saved_model()
    row=exposure['hours'][0]; weather=row['ml_weather']
    x=np.array([[np.broadcast_to(np.asarray(weather.get(f),float),(net.m,))[i] if f in WEATHER_FEATURES else b[f]
                 for f in manifest['features']] for i,b in enumerate(net.branches)],dtype=np.float32)
    model=XGBClassifier();model.load_model(MODEL_DIR/'model.ubj')
    expected=expit(calibration['coefficient']*model.predict(x,output_margin=True)+calibration['intercept'])
    assert np.allclose(pair['hours'][0]['full_probability'],expected,rtol=1e-6)
    adjusted=hybrid_weather(exposure,pair)
    for original,hybrid in zip(exposure['hours'],adjusted['hours']):
        assert sum(original['failure_probability'])==pytest.approx(sum(hybrid['failure_probability']),abs=1e-12)
    domain=np.asarray(pair['prediction']['in_training_domain'])
    assert domain.sum()==73 and (~domain).any()
    assert 'geographic transfer' in pair['prediction']['evidence_scope']
    for row in adjusted['hours']:
        d=row['hybrid_diagnostics'];unsupported=np.asarray(d['reference_peer_count'])<5
        assert np.all(np.asarray(d['hazard_multiplier'])[unsupported]==1)
    # A draw between the two probabilities must change the actual damage state,
    # rather than only the displayed probability or purple overlay.
    original=np.array([r['failure_probability'] for r in exposure['hours']])
    final=np.array([r['failure_probability'] for r in adjusted['hours']])
    t,i=np.unravel_index(np.abs(final-original).argmax(),original.shape)
    assert abs(final[t,i]-original[t,i])>1e-10
    draws=np.ones((1,net.m));draws[0,i]=(final[t,i]+original[t,i])/2
    scenario=dict(gust=np.array([exposure['hours'][t]['gust_kmh']]),rain=np.zeros((1,net.m)),draws=draws)
    eng=simulate(net,{**scenario,'failure_probability':original[t:t+1]},playback=True)
    hybrid=simulate(net,{**scenario,'failure_probability':final[t:t+1]},playback=True)
    assert (hybrid['frames'][1]['status'][i]=='weather') == (final[t,i]>original[t,i])
    assert (eng['frames'][1]['status'][i]=='weather') == (original[t,i]>final[t,i])


def test_darragh_reuses_complete_archived_predictions():
    from backend.planning import storm_context
    exposure=storm_context(nets['gb'],'darragh-2024',190,12,7,'single_peak')['planning_weather']
    pair=builtin_pair(nets['gb'],exposure,archive_storm='darragh-2024')
    assert pair['prediction']['feature_coverage']['mode']=='archived_complete_features'
    assert not pair['prediction']['feature_coverage']['missing_features']
    assert len(pair['hours'])==len(exposure['hours'])
    # Matching timestamps alone must not attach original storm scores to changed
    # user-imported weather; the caller must explicitly identify the real replay.
    imported_pair=builtin_pair(nets['gb'],exposure)
    assert imported_pair['prediction']['feature_coverage']['mode']=='partial_feature_inference'


def test_line_damage_causes_node_outages_and_repairs_restore_service():
    net=Network()
    scenario=dict(gust=np.full((2,net.m),30.),rain=np.zeros((2,net.m)),draws=np.full((2,net.m),.5),
        failure_probability=np.array([np.ones(net.m),np.zeros(net.m)]),repair_hours=np.ones((2,net.m)),
        recovery=True,crews=net.m)
    result=simulate(net,scenario,playback=True)
    first=next(f for f in result['frames'] if f['hour']==1)
    last=result['frames'][-1]
    assert first['nodes_out_of_supply']>0 and 'out_of_supply' in first['node_status']
    assert first['shed_mw']>0 and all(s=='weather' for s in first['status'])
    assert last['nodes_out_of_supply']==last['nodes_reduced_supply']==0
    assert last['served_mw']==pytest.approx(net.load.sum())
    assert result['nodes_ever_out_of_supply']>0
    assert result['failed_lines']==result['repairs']==net.m


def test_automatic_hybrid_api_produces_real_line_and_node_failures_and_replays():
    client=app.test_client()
    response=client.post('/api/storm-plan',json=dict(failure_mode='hybrid',peak_gust=190,hours=12,seed=7,samples=16,budget=1))
    assert response.status_code==200,response.json
    result=response.json
    assert result['config']['hybrid_profile'] is None
    assert result['plan']['portfolios']['none']['metrics']['mean_failed_lines']>0
    assert result['plan']['portfolios']['none']['metrics']['mean_nodes_ever_out_of_supply']>0
    assert any('weather' in f['status'] for f in result['frames'])
    assert any('out_of_supply' in f['node_status'] for f in result['frames'])
    assert result['hybrid_provenance']['prediction']['integration_method']=='relative-risk-budget-v1'
    rerun=client.post('/api/storm-plan',json={**result['replay']['request'],'custom_lines':[]})
    assert rerun.status_code==200,rerun.json
    assert result['plan']['portfolios']['none']==rerun.json['plan']['portfolios']['custom']
