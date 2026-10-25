"""Reproduce the saved-model hybrid demo without new data or training."""
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.planning import run_storm_plan
from backend.simulation import Network


def metrics_summary(metrics):
    # The UI's full run export retains asset vectors; this checked-in report
    # keeps ensemble summaries and loss draws easy to review.
    return {k:v for k,v in metrics.items() if k not in ('line_failure_probability','node_outage_probability')}


def main():
    net=Network('gb')
    run=run_storm_plan(net,peak_gust=190,hours=12,seed=7,samples=16,budget=3,crews=6,failure_mode='hybrid')
    weather=run['plan']['weather']
    frames=run['frames']
    output={
        'scope':'Synthetic wind stress test on the actual GB prototype with the saved corridor XGBoost; no new data or transmission accuracy claim',
        'request':run['replay']['request'], 'network':{'nodes':net.n,'branches':net.m},
        'provenance':run['hybrid_provenance'],
        'no_action_comparison':{k:metrics_summary(v) for k,v in run['hazard_comparison']['none'].items()},
        'portfolios':{k:{**{f:v[f] for f in ('selected','paired_gain_mwh','evidence')},'metrics':metrics_summary(v['metrics'])}
                      for k,v in run['plan']['portfolios'].items()},
        'playback':{'scenario_index':run['plan']['demo_scenario_index'],
            'peak_nodes_out_of_supply':max(f['nodes_out_of_supply'] for f in frames),
            'peak_nodes_reduced_supply':max(f['nodes_reduced_supply'] for f in frames),
            'peak_weather_damaged_lines':max(f['status'].count('weather') for f in frames),
            'peak_overload_tripped_lines':max(f['status'].count('overload') for f in frames)},
        'hourly_hazard_audit':[{'timestamp':r['timestamp'],
            'expected_engineering_failures':r['hybrid_diagnostics']['expected_engineering_failures'],
            'expected_hybrid_failures':r['hybrid_diagnostics']['expected_hybrid_failures'],
            'supported_lines':sum(n>=5 for n in r['hybrid_diagnostics']['reference_peer_count']),
            'changed_lines':int(np.count_nonzero(np.abs(np.array(r['hybrid_diagnostics']['hazard_multiplier'])-1)>1e-10))}
            for r in weather['hours']]}
    destination=ROOT/'docs/results/builtin_hybrid.json'
    destination.write_text(json.dumps(output,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'file':str(destination),'playback':output['playback'],
                      'engineering_loss_mwh':output['no_action_comparison']['engineering']['mean_unserved_mwh'],
                      'hybrid_loss_mwh':output['no_action_comparison']['hybrid']['mean_unserved_mwh'],
                      'mean_nodes_ever_out_of_supply':output['no_action_comparison']['hybrid']['mean_nodes_ever_out_of_supply']},indent=2))


if __name__=='__main__':
    main()
