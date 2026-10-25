"""Model-uncertainty sweep: rerun the same storm plan while varying one assumption at a time.

Monte Carlo standard errors only cover random draws. This shows how much the outcome and the
ranking of plans depend on assumptions nobody has calibrated: fragility median, ratings, tower
repair time, short-term rating, corridor common cause and transient fault rate. Each setting
runs in fresh worker processes (GRIDGUARD_PARAMS), with the same seed and draws.

Usage: python scripts/sensitivity_sweep.py [--scenario synthetic] [--peak-gust 190] [--samples 16]
       [--output data/sensitivity/sweep.json]
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]

SETTINGS = [
    ('baseline', {}),
    ('fragility median 230 km/h', {'pylon_median_gust': 230}),
    ('fragility median 300 km/h', {'pylon_median_gust': 300}),
    ('ratings x0.9', {'rating_scale': .9}),
    ('ratings x1.1', {'rating_scale': 1.1}),
    ('tower repair median 2 days', {'damage_repair_median_h': 48.}),
    ('tower repair median 8 days', {'damage_repair_median_h': 192.}),
    ('short-term rating 1.1x', {'short_term_rating': 1.1}),
    ('short-term rating 1.3x', {'short_term_rating': 1.3}),
    ('no corridor common cause', {'common_cause_sigma': 0.}),
    ('strong corridor common cause', {'common_cause_sigma': 1.}),
    ('transient fault rate x0.5', {'transient_rate': 5e-5}),
    ('transient fault rate x2', {'transient_rate': 2e-4}),
]


def run(setting, request):
    """One storm plan in a fresh interpreter so module-level assumptions and worker pools reset."""
    import subprocess
    env = dict(os.environ, GRIDGUARD_PARAMS=json.dumps(setting))
    code = ("import json,sys; sys.path.insert(0,'.'); from backend.app import app; "
            f"r=app.test_client().post('/api/storm-plan',json={json.dumps(request)}); "
            "assert r.status_code==200, r.get_data(as_text=True)[:400]; print(json.dumps(r.get_json()['plan']['portfolios']))")
    script = ROOT/'data/sensitivity/_run.py'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("if __name__ == '__main__':\n    " + code.replace('; ', '\n    ') + "\n")
    out = subprocess.run([sys.executable, str(script)], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def summarise(portfolios):
    rows = {k: v['metrics'] for k, v in portfolios.items()}
    ranking = sorted(rows, key=lambda k: rows[k]['mean_unserved_mwh'])
    return {'mean_unserved_mwh': rows['none']['mean_unserved_mwh'], 'p95_unserved_mwh': rows['none']['p95_unserved_mwh'],
            'standard_error_mwh': rows['none']['standard_error_mwh'],
            'mean_failed_lines': rows['none']['mean_failed_lines'], 'mean_cascade_trips': rows['none']['mean_cascade_trips'],
            'suggested_lines': portfolios['optimized']['selected'],
            'suggested_paired_gain_mwh': portfolios['optimized']['paired_gain_mwh'],
            'plan_ranking': ranking}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--scenario', default='synthetic')
    parser.add_argument('--peak-gust', type=int, default=190)
    parser.add_argument('--hours', type=int, default=36)
    parser.add_argument('--samples', type=int, default=16)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--output', type=Path, default=ROOT/'data/sensitivity/sweep.json')
    args = parser.parse_args()
    request = {'scenario': args.scenario, 'peak_gust': args.peak_gust, 'hours': args.hours, 'seed': args.seed,
               'samples': args.samples, 'budget': 5, 'crews': 6}
    results = []
    for name, setting in SETTINGS:
        started = time.time()
        row = {'setting': name, 'overrides': setting, **summarise(run(setting, request))}
        row['seconds'] = round(time.time()-started)
        results.append(row)
        print(f"{name:32s} loss {row['mean_unserved_mwh']:>10.0f} MWh  failed {row['mean_failed_lines']:5.1f}  "
              f"trips {row['mean_cascade_trips']:5.1f}  plan {row['suggested_lines']}  ranking {row['plan_ranking']}", flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'request': request, 'results': results}, indent=2))
    base = results[0]
    agree = sum(r['plan_ranking'] == base['plan_ranking'] for r in results[1:])
    same = sum(r['suggested_lines'] == base['suggested_lines'] for r in results[1:])
    print(f"Plan ranking unchanged in {agree}/{len(results)-1} settings; suggested lines unchanged in {same}/{len(results)-1}.")


if __name__ == '__main__':
    main()
