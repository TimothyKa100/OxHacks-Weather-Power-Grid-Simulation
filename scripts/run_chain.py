"""Run the whole storm chain once and save its report (same engine as POST /api/storm-plan).

ML power-cut forecast (vs reported cuts for real storms), and transmission stress: engineering
hazard -> failures and cascade -> mitigations on matched draws -> restoration -> cost-benefit ->
recommendation.

Usage:
  python scripts/run_chain.py --scenario darragh-2024
  python scripts/run_chain.py --scenario synthetic --peak-gust 190 --hours 12 --out chain.json
Set GRIDGUARD_DATA to choose the data folder (defaults to demo_data/ when built; backend/paths.py).
"""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--scenario', default='synthetic', help='synthetic or a storm id from /api/storms')
    parser.add_argument('--peak-gust', type=int, default=190)
    parser.add_argument('--hours', type=int, default=12)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--samples', type=int, default=16)
    parser.add_argument('--budget', type=int, default=3)
    parser.add_argument('--crews', type=int, default=6)
    parser.add_argument('--failure-mode', default='engineering', choices=['engineering', 'hybrid'])
    parser.add_argument('--out', type=Path, help='Write the chain report (and the full plan) here')
    args = parser.parse_args()
    from backend.paths import DATA
    from backend.planning import run_storm_plan
    from backend.simulation import Network
    started = time.time()
    result = run_storm_plan(Network('gb'), scenario=args.scenario, peak_gust=args.peak_gust, hours=args.hours, seed=args.seed,
                            samples=args.samples, budget=args.budget, crews=args.crews, failure_mode=args.failure_mode)
    report = {'data_root': str(DATA), 'request': result['replay']['request'], 'seconds': round(time.time()-started, 1), 'chain': result['chain']}
    if args.out:
        args.out.write_text(json.dumps({**report, 'plan': result['plan']}, allow_nan=False)+'\n')
    for stage in report['chain']:
        print(json.dumps(stage))
    print(json.dumps({'seconds': report['seconds'], 'data_root': report['data_root'], 'out': str(args.out) if args.out else None}))


if __name__ == '__main__':
    main()
