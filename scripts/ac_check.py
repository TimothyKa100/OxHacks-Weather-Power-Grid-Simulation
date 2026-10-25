"""AC power-flow check of a DC operating point with pandapower (optional; a dev dependency).

The cascade engine is lossless DC: it cannot see voltages, reactive power or losses. This takes
a secured DC operating point (winter evening peak by default, optionally with branches removed)
and solves it as an AC power flow, reporting convergence, voltage range and losses. A failure to
converge or voltages outside 0.9-1.1 pu flag a state the DC model treats as fine but that would
need voltage support, or could collapse. Line charging and resistance use typical values per
branch kind (illustrative, like the reactances).

Usage: python scripts/ac_check.py [--hour 18] [--gust 0] [--remove L001,L002] [--output report.json]
"""
import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Typical per-km values: (R/X ratio, capacitance nF/km). Cables have far more charging.
KIND = {'line': (.1, 12.), 'link': (.1, 12.), 'cable': (.15, 250.)}
POWER_FACTOR = .97


def ac_check(hour=18, gust=0., remove=()):
    import pandapower as pp
    from backend.simulation import Network
    from backend.grid_ops import demand_factor, secure_dispatch, solve
    net = Network('gb')
    stamp = f'2026-01-01T{hour:02d}:00:00Z'
    fraction = np.ones(net.m)
    ids = {e['id']: i for i, e in enumerate(net.branches)}
    for b in remove: fraction[ids[b]] = 0.
    demand = net.load*demand_factor([stamp], net.load.sum())[0]
    variable, firm, sync = net.grid_model.availability(np.full(net.m, gust), stamp)
    adjust = secure_dispatch(net, fraction, demand, variable, firm, sync)
    dc = solve(net, fraction, demand, variable, firm, sync, adjust)
    main = np.bincount(dc['labels']).argmax()
    keep = dc['labels'] == main  # AC check of the main synchronous island

    pnet = pp.create_empty_network(sn_mva=100.)
    bus = {}
    for b in np.flatnonzero(keep):
        bus[b] = pp.create_bus(pnet, vn_kv=float(net.buses[b]['kv']), max_vm_pu=1.1, min_vm_pu=.9)
    for i, e in enumerate(net.branches):
        a, c = e['from']-1, e['to']-1
        if fraction[i] == 0 or a not in bus or c not in bus: continue
        if e['kind'] == 'transformer':
            unit = e['rating_mw']/e['circuits']
            x_pct = e['x_pu']*e['circuits']*unit/100*100
            pp.create_transformer_from_parameters(pnet, bus[a] if net.buses[a]['kv'] >= net.buses[c]['kv'] else bus[c],
                                                  bus[c] if net.buses[a]['kv'] >= net.buses[c]['kv'] else bus[a],
                                                  sn_mva=unit, vn_hv_kv=float(max(net.buses[a]['kv'], net.buses[c]['kv'])),
                                                  vn_lv_kv=float(min(net.buses[a]['kv'], net.buses[c]['kv'])), vk_percent=x_pct,
                                                  vkr_percent=.3, pfe_kw=0., i0_percent=0., parallel=e['circuits'])
        else:
            ratio, c_nf = KIND[e['kind']]
            kv = float(e['kv']); length = max(e['length_km'], .1)
            x_ohm = e['x_pu']*e['circuits']*kv**2/100./length  # per circuit, per km
            pp.create_line_from_parameters(pnet, bus[a], bus[c], length_km=length, r_ohm_per_km=ratio*x_ohm, x_ohm_per_km=x_ohm,
                                           c_nf_per_km=c_nf, max_i_ka=e['rating_mw']/e['circuits']/(math.sqrt(3)*kv), parallel=e['circuits'])
    tan = math.tan(math.acos(POWER_FACTOR))
    for b in bus:
        if dc['served'][b] > 1e-3: pp.create_load(pnet, bus[b], p_mw=float(dc['served'][b]), q_mvar=float(dc['served'][b]*tan))
    gen = dc['generation']
    slack = max(bus, key=lambda b: gen[b])
    for b in bus:
        if b == slack:
            pp.create_ext_grid(pnet, bus[b], vm_pu=1.02)
        elif gen[b] > 1:
            pp.create_gen(pnet, bus[b], p_mw=float(gen[b]), vm_pu=1.02, min_q_mvar=-.5*gen[b], max_q_mvar=.6*gen[b])
    report = {'hour_utc': hour, 'gust_kmh': gust, 'removed': list(remove), 'buses': len(bus),
              'dc_served_mw': float(dc['served'][keep].sum())}
    try:
        pp.runpp(pnet, algorithm='nr', init='dc', enforce_q_lims=True, max_iteration=50, numba=False)
        report['reactive_limits'] = 'enforced'
    except Exception as error:  # pandapower raises LoadflowNotConverged
        # Converging only with unlimited reactive power means generators cannot hold voltage:
        # a reactive shortfall (voltage-collapse risk) rather than a data or numerical problem.
        report.update(converged_with_reactive_limits=False, error=type(error).__name__)
        try:
            pp.runpp(pnet, algorithm='nr', init='dc', enforce_q_lims=False, max_iteration=50, numba=False)
            report['reactive_limits'] = 'ignored (did not converge with them)'
        except Exception as error:
            report.update(converged=False, error_without_limits=type(error).__name__)
            return report
        q = pnet.res_gen.q_mvar.to_numpy(); qmax = pnet.gen.max_q_mvar.to_numpy()
        report['reactive_shortfall_mvar'] = float(np.clip(q-qmax, 0, None).sum())
    vm = pnet.res_bus.vm_pu
    report.update(converged=True, vm_min_pu=float(vm.min()), vm_max_pu=float(vm.max()),
                  buses_below_095=int((vm < .95).sum()), buses_above_105=int((vm > 1.05).sum()),
                  losses_mw=float(pnet.res_line.pl_mw.sum()+pnet.res_trafo.pl_mw.sum()),
                  losses_share=float((pnet.res_line.pl_mw.sum()+pnet.res_trafo.pl_mw.sum())/dc['served'][keep].sum()),
                  max_line_loading_pct=float(pnet.res_line.loading_percent.max()),
                  max_transformer_loading_pct=float(pnet.res_trafo.loading_percent.max()))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--hour', type=int, default=18)
    parser.add_argument('--gust', type=float, default=0.)
    parser.add_argument('--remove', default='')
    parser.add_argument('--output')
    args = parser.parse_args()
    result = ac_check(args.hour, args.gust, [x for x in args.remove.split(',') if x])
    print(json.dumps(result, indent=2))
    if args.output: Path(args.output).write_text(json.dumps(result, indent=2))
