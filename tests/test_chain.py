"""One storm-plan chain: ML into the hybrid, cascade, mitigations, restoration, recommendation."""
import json
import os
import subprocess
import sys
import numpy as np
from pathlib import Path
from backend.planning import run_storm_plan, storm_context
from backend.simulation import Network, recommend

ROOT = Path(__file__).resolve().parents[1]


def test_chain_runs_every_stage_and_restoration_lets_repairs_act():
    net = Network('gb')
    result = run_storm_plan(net, peak_gust=190, hours=6, seed=7, samples=16, budget=2, recovery_hours=24)
    stages = [s['stage'] for s in result['chain']]
    assert stages == ['weather', 'ml_power_cut_forecast', 'engineering_hazard', 'failures_and_cascade', 'mitigation', 'restoration', 'recommendation']
    # The ML is its own output; line failures stay on the engineering hazard by default.
    assert result['config']['failure_mode'] == 'engineering' and 'hybrid_provenance' not in result
    ml = result['chain'][1]
    assert ml['applied'] and ml['model_version'] == 'gb-corridor-xgb-v2' and 'observed_corridor_cuts' not in ml
    forecast = result['power_cut_forecast']
    assert len(forecast['probability']) == 6 and len(forecast['probability'][0]) == net.m
    plan = result['plan']
    assert 'prepared' in plan['portfolios']
    if plan['portfolios']['optimized']['selected']:
        assert plan['portfolios']['combined']['selected'] == plan['portfolios']['optimized']['selected']
    # 6 storm hours plus 24 restoration hours in 4 h steps.
    frames = plan['portfolios']['none']['playback']['frames']
    assert max(f['hour'] for f in frames) == 6+6 and max(f['elapsed_hours'] for f in frames) == 30
    assert result['replay']['request']['recovery_hours'] == 24
    rec = plan['recommendation']
    assert rec['portfolio'] in plan['portfolios'] and result['chain'][-1] == {'stage': 'recommendation', **rec}


def test_forecast_is_scored_against_reported_cuts_for_a_real_storm():
    from backend.benchmark import load_storm
    from backend.forecast import power_cut_forecast
    net = Network('gb')
    exposure = storm_context(net, 'darragh-2024', 190, 12, 7, 'single_peak')['planning_weather']
    forecast = power_cut_forecast(net, exposure, load_storm('darragh-2024'), archive_storm='darragh-2024')
    c = forecast['comparison']
    assert forecast['feature_coverage'] == 'archived_complete_features'
    assert c['lines_compared'] > 0 and c['observed_total'] > 0 and c['expected_total'] > 0
    assert len(c['observed_per_hour']) == len(exposure['hours']) == len(c['expected_per_hour'])
    # Only overhead routes in the feed's coverage are compared.
    flags = np.array([e['kind'] == 'line' for e in net.branches])
    assert not np.asarray(c['covered_lines'])[~flags].any()
    assert sum(c['observed_per_hour']) == sum(v for v in c['per_line_observed'] if v is not None)


def test_forecast_reports_why_when_route_weather_is_missing():
    net = Network('gb')
    exposure = storm_context(net, 'synthetic', 190, 6, 7, 'single_peak')['planning_weather']
    weather = {**exposure, 'hours': [{k: v for k, v in r.items() if k != 'ml_weather'} for r in exposure['hours']]}
    result = run_storm_plan(net, scenario='imported', hours=6, seed=7, samples=16, budget=1, weather=weather, recovery_hours=0)
    ml = result['chain'][1]
    assert not ml['applied'] and 'ml_weather' in ml['reason'] and 'error' in result['power_cut_forecast']


def test_recommendation_needs_an_interval_that_excludes_zero():
    def portfolio(gain, low, net):
        return {'selected': ['L1'], 'paired_gain_mwh': gain, 'evidence': {'gain_ci95_mwh': [low, 2*gain]}, 'economics': {'net_benefit_gbp': net}}
    none = portfolio(0, 0, 0)
    assert recommend({'none': none, 'a': portfolio(10, -1, 9e3)})['portfolio'] == 'none'
    assert recommend({'none': none, 'a': portfolio(10, 1, 9e3), 'b': portfolio(20, 2, 19e3), 'c': portfolio(30, -5, 29e3)})['portfolio'] == 'b'


def test_demo_data_folder_is_complete_for_the_simulation():
    """demo_data/ alone (no data/ tree) is enough for networks, storms, assets, view networks and the ML."""
    code = ("from backend.paths import DATA; assert DATA.name=='demo_data', DATA\n"
            "from backend.app import app\nc=app.test_client()\n"
            "assert c.get('/api/storms').status_code==200 and c.get('/api/network?name=gb').status_code==200\n"
            "assert c.get('/api/assets?x0=0&y0=0&x1=1&y1=1&limit=50').status_code==200\n"
            "from backend.ml_risk import v2_model; v2_model()\n")
    manifest = json.loads((ROOT/'demo_data/manifest.json').read_text())
    assert 'gb_network.json' in manifest['files'] and 'failure_model/models/gb-corridor-xgb-v2/model_full.ubj' in manifest['files']
    env = {**os.environ, 'GRIDGUARD_DATA': str(ROOT/'demo_data')}
    run = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stderr[-2000:]
