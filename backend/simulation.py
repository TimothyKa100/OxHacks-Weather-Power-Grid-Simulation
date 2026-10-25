"""GridGuard simulation. Architecture adapted from ICHack26 (MIT, Louis Tanak).

Retains Flask/NumPy step -> Monte Carlo -> mitigation comparison workflow.
All electrical and weather models below are new. DC power flow, not AC stability.
"""
import json
from functools import lru_cache
from pathlib import Path
import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import spsolve
from .weather import fragility, scenarios, load_profile, failure_probability, multiday_scenarios
from .decisions import validate_economics, cost_benefit, paired_evidence, public_guidance

PARALLEL_MIN_BRANCHES=40  # small benchmarks stay serial; process start-up would dominate
MAX_WORKERS=3  # each worker holds its own network copy and solve cache
NETWORKS={'ieee14':'data/ieee14.json','gb':'data/gb_network.json'}

class Network:
    def __init__(self, name='ieee14'):
        self.name=name
        if name.startswith('view:'):
            from .view_network import path_for  # a network reduced to a map view (view_network.py)
            source = path_for(name)
        else:
            source = Path(__file__).resolve().parents[1]/NETWORKS[name]
        self.data = json.loads(Path(source).read_text())
        self.geographic=bool(self.data.get('geographic'))
        self.buses, self.branches = self.data['buses'], self.data['branches']
        self.n, self.m = len(self.buses), len(self.branches)
        self.load = np.array([b['load_mw'] for b in self.buses])
        self.capacity, self.base_gen = np.zeros(self.n), np.zeros(self.n)
        for g in self.data['generators']:
            self.capacity[g['bus']-1] += g['max_mw']
            self.base_gen[g['bus']-1] += g['base_mw']
        self.incidence = np.zeros((self.m,self.n))
        for i,e in enumerate(self.branches):
            self.incidence[i,e['from']-1], self.incidence[i,e['to']-1] = 1., -1.
        self.ends = np.array([[e['from']-1,e['to']-1] for e in self.branches])
        self.susceptance = np.array([self.data['base_mva']/(e['x_pu']*e['tap']) for e in self.branches])
        self.cost = np.array([e['cost'] for e in self.branches])
        self.exposure = np.array([e['exposure'] for e in self.branches])
        # Per-network cache: each entry stores full flow vectors, so large grids keep far fewer.
        self.solve = lru_cache(maxsize=16000 if self.m<PARALLEL_MIN_BRANCHES else 400)(self._solve)
        base = self.solve(tuple([True]*self.m),tuple(self.load))
        if self.geographic:
            # Voltage-class ratings x circuits. No uplift to the illustrative dispatch: the GB engine
            # re-dispatches each hour, so overloads in the intact case are relieved by operators.
            self.ratings = np.array([e['rating_mw'] for e in self.branches], float)
        else:
            # case14 has no useful thermal ratings. Explicit demo assumptions, not IEEE limits.
            self.ratings = np.maximum(22.,np.abs(base['flows'])*1.55+8.)
        self.midpoints = np.array([[(self.buses[e['from']-1]['x']+self.buses[e['to']-1]['x'])/2,
                                    (self.buses[e['from']-1]['y']+self.buses[e['to']-1]['y'])/2] for e in self.branches])
        if self.geographic:
            from .grid_ops import GridModel
            self.grid_model = GridModel(self)

    def _solve(self, active_tuple, demand_tuple):
        active = np.array(active_tuple,dtype=bool)
        demand = np.array(demand_tuple)
        ends, w = self.ends[active], self.susceptance[active]
        graph = sparse.coo_matrix((np.ones(len(ends)),(ends[:,0],ends[:,1])),shape=(self.n,self.n))
        count, labels = connected_components(graph, directed=False)
        # Relabel islands in order of their lowest bus so output order is stable.
        first = np.full(count, self.n); np.minimum.at(first, labels, np.arange(self.n))
        order = np.argsort(first); rank = np.empty(count, int); rank[order] = np.arange(count); labels = rank[labels]
        components = [list(map(int, np.flatnonzero(labels==k))) for k in range(count)]
        island_sum = lambda x: np.bincount(labels, x, count)
        total, cap = island_sum(demand), island_sum(self.capacity)
        ratio = np.where(total>0, np.minimum(1., np.divide(cap, total, out=np.zeros(count), where=total>0)), 0.)
        served = demand*ratio[labels]
        target = island_sum(served)
        base = np.minimum(self.base_gen, self.capacity); base_sum = island_sum(base)
        headroom = self.capacity-base; headroom_sum = island_sum(headroom)
        scale_down = np.divide(target, base_sum, out=np.ones(count), where=base_sum>0)
        scale_up = np.divide(target-base_sum, headroom_sum, out=np.zeros(count), where=headroom_sum>0)
        down = base_sum>=target
        generation = np.where(down[labels], base*scale_down[labels], base+headroom*scale_up[labels])
        # One reference angle per island (its lowest bus); balanced active injections in MW.
        reference = np.zeros(self.n, bool); reference[first[order]] = True
        rest = np.flatnonzero(~reference)
        theta = np.zeros(self.n)
        if len(rest):
            B = sparse.coo_matrix((np.concatenate([w,w,-w,-w]),(np.concatenate([ends[:,0],ends[:,1],ends[:,0],ends[:,1]]),
                                   np.concatenate([ends[:,0],ends[:,1],ends[:,1],ends[:,0]]))),shape=(self.n,self.n)).tocsc()
            theta[rest] = spsolve(B[rest][:,rest], (generation-served)[rest])
        flows=self.susceptance*active*(self.incidence@theta)
        residual=self.incidence.T@flows-(generation-served)
        if np.max(np.abs(residual))>1e-6*max(1.,float(demand.sum())/1e3): raise RuntimeError('Power balance residual exceeds tolerance')
        return {'flows':flows,'served':served,'generation':generation,'islands':components,'residual_mw':float(np.max(np.abs(residual)))}

    def payload(self):
        return {**self.data,'name':self.name,**({'id':self.name} if self.name.startswith('view:') else {}),'total_load_mw':float(self.load.sum()),'branches':[{**{k:v for k,v in e.items() if k!='samples'},'rating_mw':round(float(self.ratings[i]),2)} for i,e in enumerate(self.branches)]}

def simulate(net, scenario, hardened=(), playback=False):
    if net.geographic and 'transient_draws' in scenario:
        # GB map: circuits, failure types, protection, re-dispatch and island viability (grid_ops).
        from .grid_ops import simulate_gb
        return simulate_gb(net, scenario, hardened, playback)
    hard=np.zeros(net.m,dtype=bool); hard[list(hardened)]=True
    active=np.ones(net.m,dtype=bool); causes=['online']*net.m
    demand=net.load.copy(); frames=[]; energy=0.; trips=0
    limits=net.ratings*np.where(hard,1.30,1.)
    recovery=scenario.get('recovery',False)
    repair_remaining=np.zeros(net.m)
    ever_failed=np.zeros(net.m,dtype=bool)
    dt=scenario.get('dt_hours',1.)
    peak_shed=0.; repairs=0; outage_hours=0.; last_outage_end=0.
    probabilities=np.zeros(net.m)
    def state(hour,kind,new_trips=(),gust=None):
        nonlocal demand
        solution=net.solve(tuple(active),tuple(demand.round(9)))
        demand=solution['served'].copy()  # shed loads stay disconnected for this event
        ratios=np.abs(solution['flows'])/limits
        if playback:
            frames.append({'hour':hour,'kind':kind,'new_trips':[net.branches[i]['id'] for i in new_trips],
                'served_mw':round(float(demand.sum()),3),'shed_mw':round(float(net.load.sum()-demand.sum()),3),
                'served_by_bus':demand.round(4).tolist(),'generation_mw':solution['generation'].round(4).tolist(),
                'flows_mw':solution['flows'].round(4).tolist(),'loading':ratios.round(4).tolist(),
                'status':causes.copy(),'islands':[[i+1 for i in c] for c in solution['islands']],
                'failure_probability':np.asarray(probabilities).round(6).tolist(),
                'timestamp':scenario.get('timestamps',[None]*len(scenario['gust']))[int(hour)-1] if hour else None,
                'gust_kmh':gust.round(1).tolist() if gust is not None else [0.]*net.m})
        return ratios
    state(0,'Intact network')
    for t in range(len(scenario['gust'])):
        gust=scenario['gust'][t]
        if recovery:
            # Crews work on a deterministic queue; unsafe local gusts pause work.
            priority=scenario.get('repair_priority',range(net.m))
            working=np.array([i for i in priority if not active[i] and gust[i] < 60][:scenario['crews']],dtype=int)
            repair_remaining[working]-=dt
            restored=working[repair_remaining[working]<=0]
            active[restored]=True
            for i in restored: causes[i]='online'
            repairs+=len(restored)
            demand=net.load.copy()  # attempt reconnection at each hourly boundary
        probabilities=fragility(gust,scenario['rain'][t],net.exposure)*np.where(hard,.25,1.)
        if recovery:
            probabilities=failure_probability(gust,scenario['rain'][t],net.exposure,dt)*np.ones(net.m)
            probabilities[hard]=failure_probability(gust[hard],scenario['rain'][t][hard],net.exposure[hard],dt,True)
        if 'failure_probability' in scenario:
            # Supplied predictions are baseline one-hour conditional probabilities.
            probabilities=1.-(1.-scenario['failure_probability'][t])**np.where(hard,.25,1.)
        weather=np.flatnonzero(active & (scenario['draws'][t]<probabilities))
        for i in weather:
            active[i]=False; causes[i]='weather'; ever_failed[i]=True
            if recovery: repair_remaining[i]=scenario['repair_hours'][t,i]
        loading=state(t+1,'Storm exposure',weather,gust)
        # Simultaneous overload trips then recompute until stable. At most m removals.
        for _ in range(net.m+1):
            overloaded=np.flatnonzero(active & (loading>1.+1e-8))
            if not len(overloaded): break
            trips+=len(overloaded)
            for i in overloaded:
                active[i]=False; causes[i]='overload'; ever_failed[i]=True
                if recovery: repair_remaining[i]=2.  # illustrative inspection/reset time
            loading=state(t+1,'Cascade trip',overloaded,gust)
        else: raise RuntimeError('Cascade failed to terminate')
        shed=float(net.load.sum()-demand.sum())
        peak_shed=max(peak_shed,shed)
        if shed>1e-5:
            outage_hours+=dt
            last_outage_end=(t+1)*dt
        energy+=shed*dt
    return {'unserved_mwh':energy,'final_shed_mw':float(net.load.sum()-demand.sum()),
            'peak_shed_mw':peak_shed,'repairs':repairs,'outage_hours':outage_hours,
            'recovery_hour':(last_outage_end+dt if outage_hours else 0.) if float(net.load.sum()-demand.sum())<=1e-5 else None,
            'failed_lines':int(ever_failed.sum()),'cascade_trips':trips,'frames':frames,
            'failed_mask':ever_failed.astype(int).tolist()}

_pool=None
_parallel_unavailable=False
_worker_nets={}

def _simulate_portfolios(args):
    name, scenario, portfolios = args
    if name not in _worker_nets: _worker_nets[name]=Network(name)
    return [simulate(_worker_nets[name],scenario,p) for p in portfolios]

def evaluate_many(net, bank, portfolios):
    """Results[portfolio][scenario]. Parallel over scenarios for large networks; same draws either way."""
    global _pool, _parallel_unavailable
    if net.m<PARALLEL_MIN_BRANCHES or len(bank)<2 or _parallel_unavailable:
        rows=[[simulate(net,s,p) for p in portfolios] for s in bank]
    else:
        if _pool is None:
            import multiprocessing, os
            from concurrent.futures import ProcessPoolExecutor
            # forkserver does not exist on Windows; spawn is the portable fallback.
            method='forkserver' if 'forkserver' in multiprocessing.get_all_start_methods() else 'spawn'
            _pool=ProcessPoolExecutor(max_workers=MAX_WORKERS,mp_context=multiprocessing.get_context(method))
        try:
            rows=list(_pool.map(_simulate_portfolios,[(net.name,s,portfolios) for s in bank]))
        except PermissionError:
            # Restricted hosts may forbid worker sockets. Identical deterministic
            # scenarios can still be evaluated locally; physical solve errors propagate.
            _parallel_unavailable=True
            _pool.shutdown(wait=False,cancel_futures=True)
            _pool=None
            rows=[[simulate(net,s,p) for p in portfolios] for s in bank]
    return [[row[k] for row in rows] for k in range(len(portfolios))]

def evaluate(net, bank, selected):
    return evaluate_many(net,bank,[selected])[0]

def mean_loss(results):
    return float(np.mean([r['unserved_mwh'] for r in results]))

def screen_candidates(net, baseline, max_candidates=24):
    """Branches worth evaluating individually. Exhaustive on small networks; on large
    ones, the lines that fail most often in the unmitigated training runs."""
    if net.m<=max_candidates: return list(range(net.m))
    frequency=np.mean([r['failed_mask'] for r in baseline],axis=0)
    return sorted(sorted(range(net.m),key=lambda i:(-frequency[i],i))[:max_candidates])

def select_reinforcement(net, bank, budget, losses=None, baseline=None, max_candidates=24):
    """Greedy marginal expected unserved-energy reduction per cost, training only.

    Large networks screen candidates first (see screen_candidates). Portfolio losses are
    cached in `losses` (keyed by sorted branch indices) so callers can share them.
    """
    losses={} if losses is None else losses
    key=lambda lines: tuple(sorted(lines))
    if baseline is None: baseline=evaluate(net,bank,[])
    losses.setdefault((),mean_loss(baseline))
    candidates=screen_candidates(net,baseline,max_candidates)
    selected=[]; remaining=budget; current=losses[()]; trace=[]
    while True:
        best=None
        options=[i for i in candidates if i not in selected and net.cost[i]<=remaining]
        todo=[i for i in options if key(selected+[i]) not in losses]
        if todo:
            for i,r in zip(todo,evaluate_many(net,bank,[selected+[i] for i in todo])): losses[key(selected+[i])]=mean_loss(r)
        for i in options:
            loss=losses[key(selected+[i])]; gain=current-loss
            item=(gain/net.cost[i],gain,-i,i,loss)
            if best is None or item[:3]>best[:3]: best=item
        if best is None or best[1]<=1e-8: break
        _,gain,_,i,loss=best; selected.append(i); remaining-=int(net.cost[i]); current=loss
        trace.append({'line':net.branches[i]['id'],'cost':int(net.cost[i]),'training_gain_mwh':round(gain,3)})
    return selected,trace

def ranked_portfolio(net, scores, budget):
    selected=[]
    for i in sorted(range(net.m),key=lambda i:(-scores[i]/net.cost[i],i)):
        if net.cost[i]<=budget: selected.append(i); budget-=int(net.cost[i])
    return selected

def summarize(results, total):
    energy=np.array([r['unserved_mwh'] for r in results]); n=len(energy)
    return {'mean_unserved_mwh':round(float(energy.mean()),3),'p95_unserved_mwh':round(float(np.quantile(energy,.95)),3),
        'standard_error_mwh':round(float(energy.std(ddof=1)/np.sqrt(n)),3),
        'blackout_probability':round(float(np.mean([r['final_shed_mw']>.5*total for r in results])),4),
        'peak_outage_probability':round(float(np.mean([r['peak_shed_mw']>.5*total for r in results])),4),
        'mean_repairs':round(float(np.mean([r['repairs'] for r in results])),3),
        'mean_outage_hours':float(np.mean([r['outage_hours'] for r in results])),
        'recovered_fraction':float(np.mean([r['recovery_hour'] is not None for r in results])),
        'worst_decile_mean_mwh':float(np.sort(energy)[-max(1,int(np.ceil(n*.1))):].mean()),
        'mean_shed_mw':round(float(np.mean([r['final_shed_mw'] for r in results])),3),
        'mean_failed_lines':round(float(np.mean([r['failed_lines'] for r in results])),3),
        'mean_cascade_trips':round(float(np.mean([r['cascade_trips'] for r in results])),3),
        'line_failure_probability':np.mean([r['failed_mask'] for r in results],axis=0).round(4).tolist(),
        'losses_mwh':energy.round(3).tolist()}

def run_experiment(net, intensity=100, seed=26, samples=128, budget=5, mode="legacy", hours=48, crews=2, weather=None,
                   storm_type='single_peak', custom_lines=None, economics=None):
    import time
    started=time.perf_counter()
    economics=validate_economics(economics)
    ids={e['id']:i for i,e in enumerate(net.branches)}
    if set(economics['asset_costs_gbp'])-set(ids): raise ValueError('Unknown asset cost IDs')
    def investment_for(lines):
        return sum(economics['asset_costs_gbp'].get(net.branches[i]['id'],float(net.cost[i])*economics['cost_per_unit_gbp']) for i in lines)
    if custom_lines is not None:
        if not isinstance(custom_lines,list) or any(not isinstance(i,str) or i not in ids for i in custom_lines):
            raise ValueError('custom_lines must contain known branch IDs')
        if len(set(custom_lines))!=len(custom_lines): raise ValueError('Duplicate custom line IDs')
        if sum(net.cost[ids[i]] for i in custom_lines)>budget: raise ValueError('Custom plan exceeds the budget')
    # View networks (backend/view_network.py) are larger, so their search uses a smaller training bank.
    training_samples,max_candidates=(6,6) if net.name.startswith('view:') else (24,24)
    if mode=='multiday':
        training=multiday_scenarios(net,intensity,seed ^ 0xA51C,training_samples,hours,weather,crews,storm_type)
        heldout=multiday_scenarios(net,intensity,seed,samples,hours,weather,crews,storm_type)
    else:
        training=scenarios(net,intensity,seed ^ 0xA51C,training_samples)
        heldout=scenarios(net,intensity,seed,samples)
    # Single-asset consequence estimates and recommendations use only the training bank.
    training_before=evaluate(net,training,[])
    training_loss=mean_loss(training_before)
    losses={():training_loss}
    # Large networks only evaluate the most frequently failing assets individually.
    candidates=screen_candidates(net,training_before,max_candidates)
    for i,r in zip(candidates,evaluate_many(net,training,[[i] for i in candidates])): losses[(i,)]=mean_loss(r)
    def kind(i):
        e=net.branches[i]
        if 'kind' in e: return e['kind']
        return 'transformer' if net.name=='ieee14' and i>=15 else 'line'  # case14 lists its 5 transformers last
    assets=[]
    for i in candidates:
        gain=training_loss-losses[(i,)]
        assets.append({'id':net.branches[i]['id'],'kind':kind(i),
                       'cost_units':int(net.cost[i]),'training_gain_mwh':gain,
                       'training_gain_per_unit':gain/float(net.cost[i]),
                       'economics':cost_benefit(gain,float(net.cost[i]),economics,investment_for([i]))})
    selected,trace=select_reinforcement(net,training,budget,losses,training_before,max_candidates)
    risk=np.mean([(failure_probability if mode=='multiday' else fragility)(s['gust'],s['rain'] if s['rain'].ndim==2 else s['rain'][:,None],net.exposure) for s in training],axis=(0,1))
    if 'failure_probability' in training[0]: risk=np.mean([s['failure_probability'] for s in training],axis=(0,1))
    # Electrical topology baseline: initial absolute flow / cost (cost applied by ranking).
    intact=net.solve(tuple([True]*net.m),tuple(net.load))
    portfolios={'none':[], 'risk':ranked_portfolio(net,risk,budget),
                'topology':ranked_portfolio(net,np.abs(intact['flows']),budget), 'optimized':selected}
    if custom_lines is not None: portfolios['custom']=[ids[i] for i in custom_lines]
    # Priority is a training-only heuristic for inspection/reset and damage repairs,
    # not geographic crew positioning. Test its impact against the original queue.
    frequency=np.mean([r['failed_mask'] for r in training_before],axis=0)
    priority=sorted(range(net.m),key=lambda i:(0,-(training_loss-losses[(i,)]),i) if (i,) in losses else (1,-frequency[i],i))
    if mode=='multiday': portfolios['prepared']=[]
    def bank_for(key):
        return [dict(s,repair_priority=priority) for s in heldout] if key=='prepared' else heldout
    result={}
    shared=[key for key in portfolios if key!='prepared']
    raw=dict(zip(shared,evaluate_many(net,heldout,[portfolios[key] for key in shared])))
    if 'prepared' in portfolios: raw['prepared']=evaluate(net,bank_for('prepared'),[])
    # Typical stress example chosen by the unmitigated median loss, before viewing intervention outcome.
    order=np.argsort([r['unserved_mwh'] for r in raw['none']]); demo=int(order[len(order)//2])
    before=np.array([r['unserved_mwh'] for r in raw['none']])
    for key,lines in portfolios.items():
        after=np.array([r['unserved_mwh'] for r in raw[key]])
        paired=before-after
        result[key]={'selected':[net.branches[i]['id'] for i in lines],'spent':int(net.cost[lines].sum()) if lines else 0,
            'metrics':summarize(raw[key],float(net.load.sum())),'paired_gain_mwh':round(float(paired.mean()),3),
            'paired_gain_standard_error_mwh':round(float(paired.std(ddof=1)/np.sqrt(samples)),3),
            'evidence':paired_evidence(before,after),
            'economics':cost_benefit(float(paired.mean()),float(net.cost[lines].sum()) if lines else 0,economics,investment_for(lines)),
            'playback':simulate(net,bank_for(key)[demo],lines,True)}
        if key=='prepared': result[key]['economics']['scope']='Queue ordering only; dispatch/travel/staff costs excluded'
    for row in assets:
        row['training_failure_frequency']=float(frequency[ids[row['id']]])
    return {'config':{'network':net.name,'intensity':intensity,'seed':seed,'samples':samples,'budget':budget,'training_samples':training_samples,'mode':mode,'hours':len(heldout[0]['gust']),'crews':crews,
                      'storm_type':storm_type,'custom_lines':custom_lines,'economics':economics},
        'network':net.payload(),'portfolios':result,'optimization_trace':trace,'weather':weather if weather is not None else (load_profile() if mode=='legacy' else {'source':'Synthetic multi-day storm; illustrative route exposure'}),
        'decisions':{'assets':sorted(assets,key=lambda a:-a['training_gain_per_unit']),
                     'repair_priority':[net.branches[i]['id'] for i in priority],
                     'repair_priority_method':'Descending single-asset hardening benefit on training scenarios (unevaluated assets after, by training failure frequency); heuristic queue ordering only',
                     'public':public_guidance(),
                     'economics_scope':'Illustrative, undiscounted event-conditional avoided outage value; not electricity market prices or annual expected losses',
                     'asset_scope':'Single-asset training estimates versus no action; do not add them together. Portfolio outcomes include interactions.'
                                   +(f' Only the {len(candidates)} most frequently failing of {net.m} assets were evaluated.' if len(candidates)<net.m else '')},
        'replay':{'weather_source':'imported' if weather is not None else 'synthetic',
                  'request':{'network':net.name,'intensity':intensity,'seed':seed,'samples':samples,'budget':budget,'mode':mode,
                             'hours':len(heldout[0]['gust']),'crews':crews,'storm_type':storm_type,
                             'weather':weather,'economics':economics,'custom_lines':custom_lines},
                  'comparison':'Same weather, failure draws, repair draws and median no-action playback scenario for all plans'},
        'demo_scenario_index':demo,'demo_selection':'Median unmitigated held-out loss; same scenario for every portfolio',
        'elapsed_seconds':round(time.perf_counter()-started,2),
        'model':{'power_flow':'Lossless DC, island-balanced active dispatch','weather':('Imported hourly exposures / uncalibrated hazard' if weather is not None else 'Synthetic / illustrative fragility'),
                 'recovery':('Limited crews; weather-gated repairs and hourly reconnection' if mode=='multiday' else 'No repairs'),
                 'failure_model':('Imported model one-hour conditional probabilities; assumed hardening hazard multiplier 0.25' if 'failure_probability' in heldout[0]
                                  else 'Hourly hazard, hardening rate multiplier 0.25' if mode=='multiday' else 'Hourly probability, hardening probability multiplier 0.25'),
                 'reinforcement':'75% lower weather failure hazard in multi-day mode (probability in original mode); 30% higher illustrative rating',
                 'objective':'Greedy training mean unserved MWh reduction / budget unit; no optimality guarantee',
                 # GB engine descriptions replace the generic ones above.
                 **({'power_flow':'Lossless DC per circuit; preventive (85% pre-fault margin) and corrective re-dispatch; lines above 1.2x short-term rating trip at once, otherwise the most overloaded trips first',
                     'operation':'Winter weekday demand curve; wind power curve with storm cut-out; interconnector imports; islands need synchronous capacity for 30% of demand',
                     'recovery':'Tower damage: crews, safe weather, lognormal repair (median 4 days); transient faults: auto-reclose (85%) or lockout reclosed remotely next hour; overload trips reclosed remotely next hour',
                     'failure_model':('Imported one-hour damage probabilities; hardened lines fall back to the assumed hazard multiplier 0.25'
                                      if 'failure_probability' in heldout[0] and 'failure_probability_hardened' not in heldout[0]
                                      else 'Route/pylon fragility damage with a per-corridor common-cause factor; per-circuit transient faults by gust'),
                     'reinforcement':'Hardened towers: fragility median x1.25 (or hazard x0.25 for imported probabilities) and half the transient fault rate; no rating change'}
                    if net.geographic and 'transient_draws' in heldout[0] else {})}}
