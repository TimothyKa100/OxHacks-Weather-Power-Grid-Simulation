"""Single-circuit (N-1) security analysis of a GB network file.

Reports the measures behind the cascade review: dead-end substations, intact loading,
rating overrides, and what each single circuit outage does (split the grid, shed load,
overload another line). A branch with several circuits loses one; a single-circuit
branch is removed. Run on any network JSON, e.g. the previous build, to compare.

Usage: python scripts/security_analysis.py [data/gb_network.json] [--output report.json]
"""
import argparse
import json
from collections import Counter
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def analyse(path):
    from backend import simulation
    from backend.grid_ops import GridModel, solve, redispatch
    simulation.NETWORKS['_audit'] = str(Path(path).resolve())
    net = simulation.Network('_audit')
    model = GridModel(net)
    degree = Counter()
    for e in net.branches: degree[e['from']] += 1; degree[e['to']] += 1
    total = model.circuits.astype(float)
    full = np.ones(net.m)
    variable, firm, sync = model.availability(np.zeros(net.m), None)
    intact = solve(net, full, net.load, variable, firm, sync)
    loading = np.abs(intact['flows'])/net.ratings
    report = {'network': str(path), 'buses': net.n, 'branches': net.m,
              'kinds': dict(Counter(e.get('kind', 'line') for e in net.branches)),
              'dead_end_substations': sum(1 for b in range(1, net.n+1) if degree[b] == 1),
              'intact_islands': int(intact['islands']), 'intact_max_loading': float(loading.max()),
              'ratings_raised_above_voltage_class': int(sum(1 for i, e in enumerate(net.branches)
                                                           if net.ratings[i] > e.get('rating_mw', net.ratings[i])+1e-6))}
    split = shed = overload = overload_after_redispatch = 0
    for i in range(net.m):
        frac = full.copy(); frac[i] = (total[i]-1)/total[i]
        s = solve(net, frac, net.load, variable, firm, sync)
        limits = net.ratings*frac
        over = (frac > 0) & (np.abs(s['flows']) > limits*(1+1e-8))
        split += s['islands'] > intact['islands']
        shed += net.load.sum()-s['served'].sum() > 1e-3
        if over.any():
            overload += 1
            delta = redispatch(net, s, limits, frac)
            if delta is None:
                overload_after_redispatch += 1
            else:
                s2 = solve(net, frac, net.load, variable, firm, sync, delta)
                overload_after_redispatch += bool(((frac > 0) & (np.abs(s2['flows']) > limits*(1+1e-6))).any())
    report.update(single_circuit_outages=net.m, outages_that_split=int(split), outages_that_shed=int(shed),
                  outages_that_overload=int(overload), outages_still_overloaded_after_redispatch=int(overload_after_redispatch))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('network', nargs='?', default=str(ROOT/'data/gb_network.json'))
    parser.add_argument('--output')
    args = parser.parse_args()
    result = analyse(args.network)
    print(json.dumps(result, indent=2))
    if args.output: Path(args.output).write_text(json.dumps(result, indent=2))
