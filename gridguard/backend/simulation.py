"""GridGuard simulation. Architecture adapted from ICHack26 (MIT, Louis Tanak).

Retains Flask/NumPy step -> Monte Carlo -> mitigation comparison workflow.
All electrical and weather models below are new. DC power flow, not AC stability.
"""
import json
from functools import lru_cache
from pathlib import Path
import numpy as np
from .weather import fragility, scenarios, load_profile

class Network:
    def __init__(self):
        self.data = json.loads((Path(__file__).resolve().parents[1]/"data/ieee14.json").read_text())
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
        self.susceptance = np.array([self.data['base_mva']/(e['x_pu']*e['tap']) for e in self.branches])
        self.cost = np.array([e['cost'] for e in self.branches])
        self.exposure = np.array([e['exposure'] for e in self.branches])
        base = self.solve(tuple([True]*self.m),tuple(self.load))
        # case14 has no useful thermal ratings. Explicit demo assumptions, not IEEE limits.
        self.ratings = np.maximum(22.,np.abs(base['flows'])*1.55+8.)

    @lru_cache(maxsize=16000)
    def solve(self, active_tuple, demand_tuple):
        active = np.array(active_tuple,dtype=bool)
        demand = np.array(demand_tuple)
        adjacency = [[] for _ in range(self.n)]
        for i,e in enumerate(self.branches):
            if active[i]:
                a,b=e['from']-1,e['to']-1
                adjacency[a].append(b); adjacency[b].append(a)
        components, seen = [],set()
        for bus in range(self.n):
            if bus in seen: continue
            stack, group = [bus],[]
            seen.add(bus)
            while stack:
                a=stack.pop(); group.append(a)
                for b in adjacency[a]:
                    if b not in seen: seen.add(b); stack.append(b)
            components.append(sorted(group))
        served, generation, theta = np.zeros(self.n),np.zeros(self.n),np.zeros(self.n)
        B = self.incidence.T @ ((self.susceptance*active)[:,None]*self.incidence)
        for group in components:
            ids=np.array(group)
            total=float(demand[ids].sum()); cap=float(self.capacity[ids].sum())
            served[ids]=demand[ids]*min(1.,cap/total) if total else 0.
            target=float(served[ids].sum())
            base=np.minimum(self.base_gen[ids],self.capacity[ids])
            if base.sum()>=target:
                output=base*(target/base.sum()) if base.sum() else base
            else:
                headroom=self.capacity[ids]-base
                output=base+headroom*((target-base.sum())/headroom.sum()) if headroom.sum() else base
            generation[ids]=output
            if len(group)>1 and cap>0:
                # One reference angle per island; balanced active injections in MW.
                rest=ids[1:]
                theta[rest]=np.linalg.solve(B[np.ix_(rest,rest)],(generation-served)[rest])
        flows=self.susceptance*active*(self.incidence@theta)
        residual=self.incidence.T@flows-(generation-served)
        if np.max(np.abs(residual))>1e-6: raise RuntimeError('Power balance residual exceeds tolerance')
        return {'flows':flows,'served':served,'generation':generation,'islands':components,'residual_mw':float(np.max(np.abs(residual)))}

    def payload(self):
        return {**self.data,'total_load_mw':float(self.load.sum()),'branches':[{**e,'rating_mw':round(float(self.ratings[i]),2)} for i,e in enumerate(self.branches)]}

def simulate(net, scenario, hardened=(), playback=False):
    hard=np.zeros(net.m,dtype=bool); hard[list(hardened)]=True
    active=np.ones(net.m,dtype=bool); causes=['online']*net.m
    demand=net.load.copy(); frames=[]; energy=0.; trips=0
    limits=net.ratings*np.where(hard,1.30,1.)
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
                'gust_kmh':gust.round(1).tolist() if gust is not None else [0.]*net.m})
        return ratios
    state(0,'Intact network')
    for t in range(len(scenario['gust'])):
        gust=scenario['gust'][t]
        probabilities=fragility(gust,scenario['rain'][t],net.exposure)*np.where(hard,.25,1.)
        weather=np.flatnonzero(active & (scenario['draws'][t]<probabilities))
        for i in weather: active[i]=False; causes[i]='weather'
        loading=state(t+1,'Storm exposure',weather,gust)
        # Simultaneous overload trips then recompute until stable. At most m removals.
        for _ in range(net.m+1):
            overloaded=np.flatnonzero(active & (loading>1.+1e-8))
            if not len(overloaded): break
            trips+=len(overloaded)
            for i in overloaded: active[i]=False; causes[i]='overload'
            loading=state(t+1,'Cascade trip',overloaded,gust)
        else: raise RuntimeError('Cascade failed to terminate')
        energy+=float(net.load.sum()-demand.sum()) # settled loss x one-hour window
    return {'unserved_mwh':energy,'final_shed_mw':float(net.load.sum()-demand.sum()),
            'failed_lines':int((~active).sum()),'cascade_trips':trips,'frames':frames,
            'failed_mask':(~active).astype(int).tolist()}

def evaluate(net, bank, selected):
    return [simulate(net,s,selected) for s in bank]

def mean_loss(results):
    return float(np.mean([r['unserved_mwh'] for r in results]))

def select_reinforcement(net, bank, budget):
    """Greedy marginal expected unserved-energy reduction per cost, training only."""
    selected=[]; remaining=budget; current=mean_loss(evaluate(net,bank,selected)); trace=[]
    while True:
        best=None
        for i in range(net.m):
            if i in selected or net.cost[i]>remaining: continue
            loss=mean_loss(evaluate(net,bank,selected+[i])); gain=current-loss
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

def summarize(results):
    energy=np.array([r['unserved_mwh'] for r in results]); n=len(energy)
    return {'mean_unserved_mwh':round(float(energy.mean()),3),'p95_unserved_mwh':round(float(np.quantile(energy,.95)),3),
        'standard_error_mwh':round(float(energy.std(ddof=1)/np.sqrt(n)),3),
        'blackout_probability':round(float(np.mean([r['final_shed_mw']>129.5 for r in results])),4),
        'mean_shed_mw':round(float(np.mean([r['final_shed_mw'] for r in results])),3),
        'mean_failed_lines':round(float(np.mean([r['failed_lines'] for r in results])),3),
        'mean_cascade_trips':round(float(np.mean([r['cascade_trips'] for r in results])),3),
        'line_failure_probability':np.mean([r['failed_mask'] for r in results],axis=0).round(4).tolist(),
        'losses_mwh':energy.round(3).tolist()}

def run_experiment(net, intensity=100, seed=26, samples=128, budget=5):
    import time
    started=time.perf_counter()
    training=scenarios(net,intensity,seed ^ 0xA51C,24)
    heldout=scenarios(net,intensity,seed,samples)
    selected,trace=select_reinforcement(net,training,budget)
    risk=np.mean([fragility(s['gust'],s['rain'][:,None],net.exposure) for s in training],axis=(0,1))
    # Electrical topology baseline: initial absolute flow / cost (cost applied by ranking).
    intact=net.solve(tuple([True]*net.m),tuple(net.load))
    portfolios={'none':[], 'risk':ranked_portfolio(net,risk,budget),
                'topology':ranked_portfolio(net,np.abs(intact['flows']),budget), 'optimized':selected}
    result={}
    raw={key:evaluate(net,heldout,lines) for key,lines in portfolios.items()}
    # Typical stress example chosen by the unmitigated median loss, before viewing intervention outcome.
    order=np.argsort([r['unserved_mwh'] for r in raw['none']]); demo=int(order[len(order)//2])
    before=np.array([r['unserved_mwh'] for r in raw['none']])
    for key,lines in portfolios.items():
        after=np.array([r['unserved_mwh'] for r in raw[key]])
        paired=before-after
        result[key]={'selected':[net.branches[i]['id'] for i in lines],'spent':int(net.cost[lines].sum()) if lines else 0,
            'metrics':summarize(raw[key]),'paired_gain_mwh':round(float(paired.mean()),3),
            'paired_gain_standard_error_mwh':round(float(paired.std(ddof=1)/np.sqrt(samples)),3),
            'playback':simulate(net,heldout[demo],lines,True)}
    return {'config':{'intensity':intensity,'seed':seed,'samples':samples,'budget':budget,'training_samples':24},
        'network':net.payload(),'portfolios':result,'optimization_trace':trace,'weather':load_profile(),
        'demo_scenario_index':demo,'demo_selection':'Median unmitigated held-out loss; same scenario for every portfolio',
        'elapsed_seconds':round(time.perf_counter()-started,2),
        'model':{'power_flow':'Lossless DC, island-balanced active dispatch','weather':'Synthetic / illustrative fragility',
                 'reinforcement':'75% lower weather-trip probability and 30% higher illustrative thermal rating',
                 'objective':'Greedy training mean unserved MWh reduction / budget unit; no optimality guarantee'}}
