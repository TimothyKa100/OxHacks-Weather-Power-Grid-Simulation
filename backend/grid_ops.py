"""Storm-time operation of the GB map network: circuits, failure types, protection, islands.

The IEEE benchmark keeps the original engine in simulation.simulate. For the geographic GB
network each hour runs, in order:

1. Remote reclose: circuits tripped by protection in the previous hour return unless the
   branch is damaged. Crews repair damaged branches when the local gust is safe.
2. Demand follows a winter weekday curve; wind output follows each bus's local wind
   through a turbine power curve (cut-out in storms); interconnectors import.
3. Failures are separated by type:
   - damage (tower collapse) takes every circuit of a branch and needs a crew for days;
     a corridor common-cause factor correlates damage on nearby routes;
   - transient faults hit single circuits; auto-reclose clears most in seconds, the rest
     lock out for the hour and are reclosed remotely next hour (no crew).
4. Protection and operators: lines above their short-term rating trip at once; lines
   between continuous and short-term rating are relieved by corrective re-dispatch
   if possible, otherwise the most overloaded trips first (time-to-trip ordering).
5. Islands need synchronous capacity for frequency response, or they collapse.

All parameters are assumptions in PARAMS; scripts/sensitivity_sweep.py varies them.
"""
from datetime import datetime

import numpy as np
from scipy import sparse
from scipy.optimize import linprog
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu

PARAMS = {
    'short_term_rating': 1.20,          # x continuous rating, tolerated for ~15 minutes
    'redispatch_rounds': 3,             # corrective re-dispatch attempts per hour before tripping
    'preventive_margin': .85,           # pre-fault dispatch loads lines to at most this share (stands in for N-1 security)
    'transient_rate': 1e-4,             # faults per circuit per 100 km per hour at the reference gust
    'transient_gust_ref_kmh': 50.,
    'transient_gust_scale_kmh': 12.,
    'autoreclose_success': .85,         # share of transient faults cleared in seconds
    'damage_repair_median_h': 96.,      # tower collapse: days, not hours
    'damage_repair_sigma': .6,
    'damage_repair_bounds_h': (24., 504.),
    'crew_safe_gust_kmh': 60.,
    'synchronous_share': .30,           # island needs this share of its demand as synchronous capacity
    'gust_factor': 1.4,                 # 10 m gust / 10 m mean wind
    'hub_factor': 1.25,                 # hub-height (~100 m) / 10 m mean wind
    'wind_cut_in_ms': 3., 'wind_rated_ms': 12., 'wind_cut_out_ms': 25.,
    'hardening_median_factor': 1.25,    # hardened towers: fragility median x 1.25
    'hardening_transient_factor': .5,   # hardened circuits: half the transient fault rate
    'common_cause_sigma': .5,           # lognormal sigma of the per-corridor, per-hour damage factor
    'corridor_cell_deg': .5,
    'rating_scale': 1.,
}


def apply_overrides():
    """Sensitivity sweeps set GRIDGUARD_PARAMS (JSON) so worker processes use the same assumptions."""
    import json, os
    overrides = json.loads(os.environ.get('GRIDGUARD_PARAMS', '{}'))
    for key, value in overrides.items():
        if key != 'pylon_median_gust' and key not in PARAMS: raise KeyError(f'Unknown model parameter {key}')
        PARAMS[key] = tuple(value) if isinstance(PARAMS.get(key), tuple) else value
    return overrides


# Winter weekday GB transmission demand (GW) by UTC hour; illustrative shape from NESO data.
WINTER_DEMAND_GW = [30, 28, 27, 26, 26, 27, 30, 35, 38, 38, 37, 36, 35, 35, 35, 36, 39, 43, 44, 42, 39, 36, 34, 32]
FIRM_AVAILABILITY = {'nuclear': .85, 'gas': .90, 'coal': 0., 'hydro': .80, 'pumped': .90, 'bioenergy': .85,
                     'waste': .80, 'oil': .80, 'storage': .50, 'interconnector': .80, 'marine': .30,
                     'other': .50, 'boundary': 1.}
SYNCHRONOUS = {'nuclear', 'gas', 'coal', 'hydro', 'pumped', 'bioenergy', 'waste', 'oil', 'boundary'}
WIND = {'wind_onshore', 'wind_offshore'}
OVERHEAD = {'line', None}
_OVERRIDES = apply_overrides()


def demand_factor(timestamps, base_total_mw):
    """Hourly multiplier on the network's demand shape (base total ~30 GW)."""
    out = []
    for stamp in timestamps:
        hour = datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).hour if stamp else 18
        out.append(WINTER_DEMAND_GW[hour]*1000/base_total_mw)
    return np.array(out)


def wind_power_fraction(gust_kmh):
    p = PARAMS
    hub = np.asarray(gust_kmh, float)/p['gust_factor']*p['hub_factor']/3.6
    ramp = np.clip((hub**3-p['wind_cut_in_ms']**3)/(p['wind_rated_ms']**3-p['wind_cut_in_ms']**3), 0, 1)
    return np.where(hub >= p['wind_cut_out_ms'], 0., ramp)


def solar_fraction(timestamps):
    hours = np.array([datetime.fromisoformat(str(s).replace('Z', '+00:00')).hour if s else 12 for s in timestamps])
    return np.clip(np.sin(np.pi*(hours-8)/8), 0, 1)*.15  # low winter sun, 08-16 UTC


class GridModel:
    """Per-network constants: circuits, generation by fuel class, corridor groups, factorised solves."""

    def __init__(self, net):
        self.net = net
        b = net.branches
        self.circuits = np.array([max(1, int(e.get('circuits', 1))) for e in b])
        self.kind = [e.get('kind', 'line') for e in b]
        self.overhead = np.array([k in OVERHEAD for k in self.kind])
        self.length = np.array([float(e.get('length_km', 0.)) for e in b])
        self.wind_cap, self.solar_cap, self.firm_cap, self.sync_cap = (np.zeros(net.n) for _ in range(4))
        for g in net.data['generators']:
            i = g['bus']-1
            for fuel, mw in (g.get('fuels') or {'other': g['max_mw']}).items():
                if fuel in WIND: self.wind_cap[i] += mw
                elif fuel == 'solar': self.solar_cap[i] += mw
                else:
                    self.firm_cap[i] += mw*FIRM_AVAILABILITY.get(fuel, .5)
                    if fuel in SYNCHRONOUS: self.sync_cap[i] += mw*FIRM_AVAILABILITY.get(fuel, .5)
        cell = PARAMS['corridor_cell_deg']
        lonlat = [((net.buses[e['from']-1]['lon']+net.buses[e['to']-1]['lon'])/2,
                   (net.buses[e['from']-1]['lat']+net.buses[e['to']-1]['lat'])/2) for e in b]
        keys = [(int(np.floor(x/cell)), int(np.floor(y/cell))) for x, y in lonlat]
        index = {k: i for i, k in enumerate(sorted(set(keys)))}
        self.corridor = np.array([index[k] for k in keys])
        self.groups = len(index)
        # Mean gust of the branches meeting at each bus (sparse averaging matrix, bus x branch).
        rows = np.concatenate([net.ends[:, 0], net.ends[:, 1]]); cols = np.tile(np.arange(net.m), 2)
        degree = np.bincount(rows, minlength=net.n).astype(float)
        self.bus_mean = sparse.csr_matrix((1/np.maximum(degree[rows], 1), (rows, cols)), shape=(net.n, net.m))

    def bus_gust(self, gust):
        return self.bus_mean@np.asarray(gust, float)

    def availability(self, gust, timestamp):
        wind = self.wind_cap*wind_power_fraction(self.bus_gust(gust))
        solar = self.solar_cap*solar_fraction([timestamp])[0]
        return wind+solar, self.firm_cap, self.sync_cap


def solve(net, fraction, demand, variable, firm, sync, adjust=None):
    """Lossless DC flow with island viability and merit order: variable output first, firm fills in."""
    active = fraction > 0
    ends = net.ends[active]
    graph = sparse.coo_matrix((np.ones(len(ends)), (ends[:, 0], ends[:, 1])), shape=(net.n, net.n))
    count, labels = connected_components(graph, directed=False)
    isum = lambda x: np.bincount(labels, x, count)
    D, V, F, S = isum(demand), isum(variable), isum(firm), isum(sync)
    collapsed = (D > 1e-6) & (S < PARAMS['synchronous_share']*D)
    supply = np.where(collapsed, 0., V+F)
    ratio = np.where(D > 0, np.minimum(1., np.divide(supply, D, out=np.zeros(count), where=D > 0)), 0.)
    served = demand*ratio[labels]
    target = isum(served)
    use_v = np.minimum(V, target)
    v_share = np.divide(use_v, V, out=np.zeros(count), where=V > 0)
    f_share = np.divide(target-use_v, F, out=np.zeros(count), where=F > 0)
    generation = variable*v_share[labels]+firm*f_share[labels]
    if adjust is not None:
        # A re-dispatch made before a trip stays in force; if the trip split the grid, each new
        # island rebalances by scaling its own generation to its served demand.
        adjusted = np.clip(generation+adjust, 0., variable+firm)
        produced = isum(adjusted)
        keep = produced > 1e-9  # an island the change left without generation keeps its pro-rata dispatch
        scale = np.divide(target, produced, out=np.ones(count), where=keep)
        generation = np.where(keep[labels], adjusted*scale[labels], generation)
    w = net.susceptance*fraction
    first = np.full(count, net.n); np.minimum.at(first, labels, np.arange(net.n))
    reference = np.zeros(net.n, bool); reference[first] = True
    rest = np.flatnonzero(~reference)
    theta = np.zeros(net.n); lu = None
    if len(rest):
        aw = w[active]
        B = sparse.coo_matrix((np.concatenate([aw, aw, -aw, -aw]), (np.concatenate([ends[:, 0], ends[:, 1], ends[:, 0], ends[:, 1]]),
                               np.concatenate([ends[:, 0], ends[:, 1], ends[:, 1], ends[:, 0]]))), shape=(net.n, net.n)).tocsc()
        lu = splu(B[rest][:, rest].tocsc())
        theta[rest] = lu.solve((generation-served)[rest])
    flows = w*(net.incidence@theta)
    residual = net.incidence.T@flows-(generation-served)
    if np.max(np.abs(residual)) > 1e-5*max(1., float(demand.sum())/1e3): raise RuntimeError('Power balance residual exceeds tolerance')
    return {'flows': flows, 'served': served, 'generation': generation, 'labels': labels, 'islands': count,
            'collapsed': int(collapsed.sum()), 'lu': lu, 'rest': rest, 'w': w,
            'headroom': firm*(1-f_share[labels])}


def redispatch(net, solution, limits, fraction, penalty=1e4):
    """Re-dispatch: shift generation to bring monitored lines within `limits`, moving as few MW as
    possible. Limits are soft (heavily penalised slack), so when they cannot all be met the change
    minimises the remaining excess. Returns the generation change, or None if nothing can move."""
    flows, lu, rest = solution['flows'], solution['lu'], solution['rest']
    if lu is None: return None
    active = fraction > 0
    loading = np.abs(flows)/np.maximum(limits, 1e-9)
    monitored = np.flatnonzero(active & (loading > .8))
    if not len(monitored): return np.zeros(net.n)
    position = np.full(net.n, -1); position[rest] = np.arange(len(rest))
    rows = []
    for l in monitored:
        i, j = net.ends[l]
        rhs = np.zeros(len(rest))
        if position[i] >= 0: rhs[position[i]] += 1
        if position[j] >= 0: rhs[position[j]] -= 1
        x = np.zeros(net.n); x[rest] = lu.solve(rhs)
        rows.append(solution['w'][l]*x)
    ptdf = np.array(rows)
    gen = solution['generation']
    movable = np.flatnonzero((gen > 1e-6) | (solution['headroom'] > 1e-6))
    if not len(movable): return None
    up_max, down_max = solution['headroom'][movable], gen[movable]
    k = len(movable)
    # Variables: up (k), down (k), relative excess per monitored line (nl), worst excess (1).
    # Lexicographic by weights: worst relative excess, then total excess, then MW moved.
    nl = len(monitored)
    lim = np.maximum(limits[monitored], 1e-6)
    cost = np.concatenate([np.ones(2*k), np.full(nl, penalty), [100*penalty]])
    sens = ptdf[:, movable]
    excess = -np.diag(lim)  # slack in MW = relative excess x limit
    zero = np.zeros((nl, 1))
    A_ub = np.vstack([np.hstack([sens, -sens, excess, zero]), np.hstack([-sens, sens, excess, zero]),
                      np.hstack([np.zeros((nl, 2*k)), np.eye(nl), -np.ones((nl, 1))])])
    b_ub = np.concatenate([lim-flows[monitored], lim+flows[monitored], np.zeros(nl)])
    labels = solution['labels'][movable]
    islands = np.unique(labels)
    A_eq = np.array([np.concatenate([(labels == c).astype(float), -(labels == c).astype(float), np.zeros(nl+1)]) for c in islands])
    result = linprog(cost, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=np.zeros(len(islands)),
                     bounds=list(zip(np.zeros(2*k+nl+1), np.concatenate([up_max, down_max, np.full(nl+1, np.inf)]))), method='highs')
    if result.status != 0: return None
    delta = np.zeros(net.n); delta[movable] = result.x[:k]-result.x[k:2*k]
    return delta


def secure_dispatch(net, fraction, demand, variable, firm, sync, rounds=5):
    """Pre-fault generation change keeping every line within the preventive margin of its rating,
    or as close to it as the network allows (soft limits).

    Full N-1 (SQSS) security needs a contingency analysis every hour; a pre-fault margin is the
    cheap stand-in. Returns the best change found (None if nothing needed or nothing helps).
    """
    adjust = None
    limits = net.ratings*PARAMS['rating_scale']*fraction*PARAMS['preventive_margin']
    for _ in range(rounds):
        solution = solve(net, fraction, demand, variable, firm, sync, adjust)
        if not ((fraction > 0) & (np.abs(solution['flows']) > limits*(1+1e-6))).any(): break
        delta = redispatch(net, solution, limits, fraction)
        if delta is None or np.abs(delta).sum() < 1e-3: break
        adjust = delta if adjust is None else adjust+delta
    return adjust


def cached_secure_dispatch(net, fraction, demand, variable, firm, sync):
    """Plans compared on one scenario share most hourly states, so reuse their preventive dispatch."""
    cache = net.__dict__.setdefault('_secure_cache', {})
    key = (fraction.tobytes(), np.round(demand, 3).tobytes(), np.round(variable, 3).tobytes(), PARAMS['preventive_margin'], PARAMS['rating_scale'])
    if key not in cache:
        if len(cache) > 4000: cache.clear()
        cache[key] = secure_dispatch(net, fraction, demand, variable, firm, sync)
    return cache[key]


def damage_repair_hours(rng, shape):
    p = PARAMS
    low, high = p['damage_repair_bounds_h']
    return np.clip(rng.lognormal(np.log(p['damage_repair_median_h']), p['damage_repair_sigma'], shape), low, high)


def transient_probability(model, gust, hardened):
    p = PARAMS
    rate = p['transient_rate']*model.length/100*np.exp((np.asarray(gust)-p['transient_gust_ref_kmh'])/p['transient_gust_scale_kmh'])
    rate = rate*np.where(hardened, p['hardening_transient_factor'], 1.)*model.overhead
    return -np.expm1(-np.minimum(rate, 50.))


def simulate_gb(net, scenario, hardened=(), playback=False):
    model = net.grid_model
    m, p = net.m, PARAMS
    hard = np.zeros(m, bool); hard[list(hardened)] = True
    total = model.circuits.copy()
    damaged = np.zeros(m, bool)
    out_trip = np.zeros(m, int)          # circuits out after protection/overload trips, reclosed next hour
    repair_remaining = np.zeros(m)
    ever_failed = np.zeros(m, bool)
    limits_full = net.ratings*p['rating_scale']
    hours = len(scenario['gust'])
    timestamps = scenario.get('timestamps') or [None]*hours
    factor = demand_factor(timestamps, float(net.load.sum()))
    stats = dict(transient_faults=0, reclosed_transients=0, lockouts=0, damage=0, overload_trips=0,
                 preventive_mw=0., redispatch_mw=0., island_collapses=0)
    energy = peak_shed = outage_hours = last_end = 0.; repairs = 0; trips = 0
    frames = []
    probabilities = np.zeros(m)
    status = ['online']*m

    def fraction():
        in_service = np.where(damaged, 0, total-out_trip)
        return np.clip(in_service, 0, None)/total

    def record(hour, kind, solution, demand, gust, new=()):
        if not playback: return
        frac = fraction()
        limits = limits_full*frac
        frames.append({'hour': hour, 'kind': kind, 'new_trips': [net.branches[i]['id'] for i in new],
                       'served_mw': round(float(solution['served'].sum()), 3),
                       'shed_mw': round(float(demand.sum()-solution['served'].sum()), 3),
                       'served_by_bus': solution['served'].round(4).tolist(), 'generation_mw': solution['generation'].round(4).tolist(),
                       'flows_mw': solution['flows'].round(4).tolist(),
                       'loading': np.where(limits > 0, np.abs(solution['flows'])/np.maximum(limits, 1e-9), 0.).round(4).tolist(),
                       'status': status.copy(), 'circuits_in_service': (frac*total).round().astype(int).tolist(),
                       'islands': [[int(b)+1 for b in np.flatnonzero(solution['labels'] == c)] for c in range(solution['islands'])],
                       'failure_probability': np.asarray(probabilities).round(6).tolist(),
                       'timestamp': timestamps[int(hour)-1] if hour else None,
                       'gust_kmh': np.asarray(gust).round(1).tolist() if gust is not None else [0.]*m,
                       'demand_mw': round(float(demand.sum()), 1)})

    zero = np.zeros(m)
    variable, firm, sync = model.availability(zero, timestamps[0])
    demand0 = net.load*factor[0]
    record(0, 'Intact network', solve(net, fraction(), demand0, variable, firm, sync), demand0, None)
    for t in range(hours):
        gust = scenario['gust'][t]
        # 1. Remote reclose of protection trips; weather-gated crew repairs of damage.
        out_trip[:] = 0
        for i in range(m):
            if status[i] in ('overload', 'transient'): status[i] = 'online'
        priority = scenario.get('repair_priority', range(m))
        working = np.array([i for i in priority if damaged[i] and gust[i] < p['crew_safe_gust_kmh']][:scenario['crews']], int)
        repair_remaining[working] -= scenario.get('dt_hours', 1.)
        restored = working[repair_remaining[working] <= 0]
        damaged[restored] = False
        for i in restored: status[i] = 'online'
        repairs += len(restored)
        # 2. Demand and generation for this hour.
        demand = net.load*factor[t]
        variable, firm, sync = model.availability(gust, timestamps[t])
        # Preventive dispatch: operators secure the pre-fault state for this hour's demand and wind.
        preventive = cached_secure_dispatch(net, fraction(), demand, variable, firm, sync)
        if preventive is not None: stats['preventive_mw'] += float(np.abs(preventive).sum()/2)
        # 3a. Damage: whole branch, correlated within a corridor.
        if 'failure_probability' in scenario:
            base = scenario['failure_probability'][t]
            # Imported ML probabilities have no fragility model: fall back to the assumed hazard x 0.25.
            hard_p = scenario['failure_probability_hardened'][t] if 'failure_probability_hardened' in scenario else 1-(1-base)**.25
        else:
            from .weather import failure_probability
            base = failure_probability(gust, scenario['rain'][t], net.exposure)*model.overhead
            hard_p = failure_probability(gust, scenario['rain'][t], net.exposure, hardened=True)*model.overhead
        probabilities = np.where(hard, hard_p, base)
        z = scenario['common_cause'][t][model.corridor] if 'common_cause' in scenario else 1.
        effective = -np.expm1(np.log1p(-np.minimum(probabilities, 1-1e-12))*z)
        new_damage = np.flatnonzero(~damaged & (scenario['draws'][t] < effective))
        for i in new_damage:
            damaged[i] = True; status[i] = 'weather'; ever_failed[i] = True
            repair_remaining[i] = scenario['repair_hours'][t, i]
        stats['damage'] += len(new_damage)
        # 3b. Transient faults on single circuits of overhead lines still standing.
        q = transient_probability(model, gust, hard)
        draws = scenario['transient_draws'][t]
        faults = (draws[:, :model.circuits.max()] < q[:, None]) & (np.arange(model.circuits.max())[None, :] < total[:, None])
        faults &= ~damaged[:, None]
        n_faults = faults.sum(axis=1)
        stats['transient_faults'] += int(n_faults.sum())
        locked = (faults & (scenario['reclose_draws'][t][:, :faults.shape[1]] > p['autoreclose_success'])).sum(axis=1)
        stats['reclosed_transients'] += int(n_faults.sum()-locked.sum())
        stats['lockouts'] += int(locked.sum())
        out_trip += locked
        for i in np.flatnonzero(locked >= total): status[i] = 'transient'; ever_failed[i] = True
        # 4. Protection and corrective re-dispatch until secure or stable.
        adjust = preventive; rounds = 0; new_trips = []
        for _ in range(2*m+2):
            frac = fraction()
            solution = solve(net, frac, demand, variable, firm, sync, adjust)
            limits = limits_full*frac
            loading = np.abs(solution['flows'])/np.maximum(limits, 1e-9)
            active = frac > 0
            beyond = np.flatnonzero(active & (loading > p['short_term_rating']+1e-8))
            over = np.flatnonzero(active & (loading > 1+1e-8))
            if len(beyond):
                tripped = beyond
            elif len(over) and rounds < p['redispatch_rounds']:
                delta = redispatch(net, solution, limits, frac)
                rounds += 1
                if delta is not None:
                    adjust = delta if adjust is None else adjust+delta
                    stats['redispatch_mw'] += float(np.abs(delta).sum()/2)
                    continue
                tripped = over[[int(np.argmax(loading[over]))]]
            elif len(over):
                tripped = over[[int(np.argmax(loading[over]))]]
            else:
                break
            for i in tripped:
                out_trip[i] = total[i]; status[i] = 'overload'; ever_failed[i] = True
            trips += len(tripped); new_trips += list(tripped)
            record(t+1, 'Cascade trip', solution, demand, gust, tripped)
        else:
            raise RuntimeError('Cascade failed to terminate')
        stats['overload_trips'] += len(new_trips)
        stats['island_collapses'] += solution['collapsed']
        record(t+1, 'Settled', solution, demand, gust, list(new_damage))
        shed = float(demand.sum()-solution['served'].sum())
        peak_shed = max(peak_shed, shed)
        if shed > 1e-5:
            outage_hours += 1; last_end = t+1
        energy += shed
    final_shed = shed if hours else 0.
    return {'unserved_mwh': energy, 'final_shed_mw': final_shed, 'peak_shed_mw': peak_shed, 'repairs': repairs,
            'outage_hours': outage_hours, 'recovery_hour': (last_end+1 if outage_hours else 0.) if final_shed <= 1e-5 else None,
            'failed_lines': int(ever_failed.sum()), 'cascade_trips': trips, 'frames': frames,
            'failed_mask': ever_failed.astype(int).tolist(), **stats}
