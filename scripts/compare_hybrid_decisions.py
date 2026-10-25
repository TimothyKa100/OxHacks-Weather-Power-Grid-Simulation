"""Engineering vs v2 hybrid reinforcement decisions on matched draws (docs/results/hybrid_decisions_v2.json).

Both hazards use the same seed, so each Monte Carlo draw is shared. Each side's chosen lines are
re-evaluated under the other hazard (custom portfolio), so a different choice shows its cost.
"""
import json
from pathlib import Path
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.planning import run_storm_plan
from backend.simulation import Network

CASES = [dict(scenario='synthetic', peak_gust=190, hours=12, seed=7), dict(scenario='darragh-2024', peak_gust=190, hours=60, seed=7)]
KEYS = ('mean_unserved_mwh', 'p95_unserved_mwh', 'mean_failed_lines', 'mean_nodes_ever_out_of_supply')


def short(metrics): return {k: round(float(metrics[k]), 3) for k in KEYS}


def main(samples=16, budget=3):
    net = Network('gb'); out = []
    for case in CASES:
        started = time.time()
        run = run_storm_plan(net, **case, samples=samples, budget=budget, failure_mode='hybrid')
        eng, hyb = run['engineering_plan']['portfolios'], run['plan']['portfolios']
        diag = [r['hybrid_diagnostics'] for r in run['plan']['weather']['hours']]
        multipliers = np.array([d['hazard_multiplier'] for d in diag])
        row = {**case, 'model': run['hybrid_provenance']['prediction']['model_version'],
               'feature_coverage': run['hybrid_provenance']['prediction']['feature_coverage']['mode'],
               'lines_tilted': int((np.abs(multipliers-1) > 1e-10).any(axis=0).sum()),
               'multiplier_range': [round(float(multipliers.min()), 3), round(float(multipliers.max()), 3)],
               'no_action': {'engineering': short(eng['none']['metrics']), 'hybrid': short(hyb['none']['metrics'])}, 'portfolios': {}}
        for key in ('risk', 'optimized'):
            e, h = eng[key]['selected'], hyb[key]['selected']
            entry = {'engineering_choice': e, 'hybrid_choice': h, 'same': sorted(e) == sorted(h)}
            if not entry['same']:
                # Cross-evaluate both choices under both hazards on the same draws.
                for name, lines in (('engineering_choice', e), ('hybrid_choice', h)):
                    c = run_storm_plan(net, **case, samples=samples, budget=budget, failure_mode='hybrid', custom_lines=lines)['hazard_comparison']['custom']
                    entry[f'{name}_under'] = {k: short(v) for k, v in c.items()}
            row['portfolios'][key] = entry
        row['seconds'] = round(time.time()-started, 1)
        print(json.dumps(row), flush=True); out.append(row)
    destination = ROOT/'docs/results/hybrid_decisions_v2.json'
    destination.write_text(json.dumps({'scope': 'GB prototype network; v2 corridor model through the bounded hybrid (beta 0.5, multiplier 0.5-2, hourly expected failures preserved)',
                                       'samples': samples, 'budget': budget, 'cases': out}, indent=2)+'\n')


if __name__ == '__main__':
    main()
