"""Single-storm simulation on the geographic network: wind -> pylons -> lines -> power flow.

A line's risk comes from how many pylons hold it up and where they stand.
Pylon count is route length / typical span (see scripts/build_gb_network.py),
spread evenly over sample points every ~2 km along the OSM route. Each sample
has a terrain height; local gust is scaled by the UK National Annex to
BS EN 1991-1-4 altitude factor, so hill-top pylons see stronger wind. Each
pylon has a lognormal fragility curve against its gust; one collapse drops the
line, and it stays down for the rest of the storm. The DC power flow then recomputes
and overloaded lines trip in batches until stable. Overload trips are not
damage: those lines are reclosed at the next hour.
"""
import numpy as np
from scipy.special import ndtr

PYLON_MEDIAN_GUST = 265.   # km/h (at the pylon, after altitude) at which half would fail within an hour (illustrative)
PYLON_BETA = .12           # lognormal dispersion of pylon strength (illustrative)
STORM_SIGMA = .11          # storm core radius in map units (~120 km on GB)
GUSTINESS = .08            # hour-to-hour, place-to-place gust noise (lognormal sigma)


def pylon_failure_probability(gust_kmh):
    """Probability one pylon collapses during an hour with this peak local gust."""
    gust = np.maximum(np.asarray(gust_kmh, float), 1e-6)
    return ndtr(np.log(gust/PYLON_MEDIAN_GUST)/PYLON_BETA)


def altitude_factor(elevation_m, height_m):
    """UK NA to BS EN 1991-1-4: c_alt = 1 + 0.001 A (10/z)^0.2 for z > 10 m (A = site altitude, m)."""
    return 1+.001*np.asarray(elevation_m)*(10/np.maximum(np.asarray(height_m), 10.))**.2


def route_samples(net):
    """Flattened route samples: position, terrain height, owning line and pylon share."""
    if not hasattr(net, '_route_samples'):
        rows = [(i, x, y, ele, e['pylons']/len(e['samples']), e['tower_height_m'])
                for i, e in enumerate(net.branches) for x, y, ele in e['samples']]
        line, x, y, ele, pylons, height = map(np.array, zip(*rows))
        net._route_samples = {'line': line.astype(int), 'xy': np.column_stack([x, y]), 'ele': ele,
                              'pylons': pylons, 'c_alt': altitude_factor(ele, height)}
    return net._route_samples


def storm_path(net, hours, rng):
    """Storm centre per hour: crosses the network south-west to north-east, with a seeded wobble."""
    xy = np.array([[b['x'], b['y']] for b in net.buses])
    (x0, y0), (x1, y1) = xy.min(axis=0), xy.max(axis=0)
    start = np.array([x0+(x1-x0)*rng.uniform(-.1, .3), y1+(y1-y0)*.05])
    end = np.array([x0+(x1-x0)*rng.uniform(.7, 1.1), y0+(y1-y0)*rng.uniform(.0, .3)])
    progress = np.linspace(0, 1, hours)[:, None]
    centre = start+(end-start)*progress
    centre[:, 0] += .03*np.sin(progress[:, 0]*np.pi*rng.uniform(1, 3))
    strength = .35+.65*np.sin(np.pi*np.linspace(.05, .95, hours))  # deepens, then fills
    return centre, strength


def simulate_gusts(net, hours, gust_at, rng, marker=lambda t: (None, 0.)):
    """Core engine. gust_at(t) -> gust (km/h, at the pylon) for every route sample in hour t.

    Returns hourly frames plus the per-sample collapse hazard of every hour (for benchmarking).
    """
    samples = route_samples(net)
    line_of = samples['line']
    active = np.ones(net.m, bool)
    status = ['online']*net.m
    fallen_at = [None]*net.m
    demand = net.load.copy()
    frames, hazards = [], []

    def snapshot(hour, gust, line_probability, events, centre_xy, radius):
        nonlocal demand
        solution = net.solve(tuple(active), tuple(demand.round(9)))
        demand = solution['served'].copy()
        loading = np.abs(solution['flows'])/net.ratings
        frames.append({'hour': hour, 'events': events, 'storm': centre_xy, 'storm_radius': radius,
                       'status': status.copy(), 'fallen_at': fallen_at.copy(),
                       'gust_kmh': gust.round(1).tolist(), 'line_failure_probability': line_probability.round(5).tolist(),
                       'flows_mw': solution['flows'].round(2).tolist(), 'loading': loading.round(4).tolist(),
                       'served_by_bus': demand.round(2).tolist(),
                       'shed_mw': round(float(net.load.sum()-demand.sum()), 1),
                       'islands': len(solution['islands'])})
        return loading

    snapshot(0, np.zeros(net.m), np.zeros(net.m), [], None, 0.)
    for t in range(hours):
        events = []
        # Protection trips are not damage: reclose them and reconnect load each hour.
        reclosed = [i for i in range(net.m) if status[i] == 'overload']
        for i in reclosed: active[i] = True; status[i] = 'online'
        if reclosed: events.append({'type': 'reclose', 'lines': [net.branches[i]['id'] for i in reclosed]})
        demand = net.load.copy()

        gust = gust_at(t)
        # Hazard per sample = its pylons times -log(survival of one pylon); a line sums its samples.
        hazard = -samples['pylons']*np.log1p(-np.minimum(pylon_failure_probability(gust), 1-1e-12))
        hazards.append(hazard)
        line_probability = -np.expm1(-np.bincount(line_of, hazard, net.m))
        fell = np.flatnonzero(active & (rng.random(net.m) < line_probability))
        for i in fell:
            own = np.flatnonzero(line_of == i)
            k = own[rng.choice(len(own), p=hazard[own]/hazard[own].sum())]
            fallen_at[i] = [round(float(c), 5) for c in samples['xy'][k]]+[float(samples['ele'][k])]  # x, y, terrain m
            active[i] = False; status[i] = 'pylon'
            events.append({'type': 'pylon', 'line': net.branches[i]['id'], 'at': fallen_at[i],
                           'ele_m': float(samples['ele'][k]), 'gust_kmh': round(float(gust[k]), 1)})
        line_gust = np.zeros(net.m); np.maximum.at(line_gust, line_of, gust)
        storm_xy, radius = marker(t)
        loading = snapshot(t+1, line_gust, line_probability, events, storm_xy, radius)
        for _ in range(net.m+1):
            over = np.flatnonzero(active & (loading > 1+1e-8))
            if not len(over): break
            for i in over: active[i] = False; status[i] = 'overload'
            events.append({'type': 'overload', 'lines': [net.branches[i]['id'] for i in over]})
            frames.pop()  # keep one settled frame per hour
            loading = snapshot(t+1, line_gust, line_probability, events, storm_xy, radius)
        else: raise RuntimeError('Cascade failed to terminate')
    return frames, np.array(hazards)


def model_info(net):
    return {'pylon_median_gust_kmh': PYLON_MEDIAN_GUST, 'pylon_beta': PYLON_BETA,
            'altitude_factor': 'c_alt = 1 + 0.001 x elevation x (10 / tower height)^0.2',
            'route_samples': int(len(route_samples(net)['line']))}


def planning_exposure(net, frames, hazards, timestamps, source):
    """Full-precision route risk for the shared multi-day decision engine.

    Engineering probabilities are labelled separately from trained ML predictions.
    Sampling and repairs are performed by simulation.simulate, never by the map.
    """
    line_of=route_samples(net)['line']
    return {'source':source, 'branch_ids':[e['id'] for e in net.branches],
            'engineering_model':{'name':'route-pylon-v1','parameters':model_info(net)},
            'hours':[{'timestamp':timestamps[t], 'gust_kmh':frames[t+1]['gust_kmh'], 'rain_mm_h':0,
                      'failure_probability':(-np.expm1(-np.bincount(line_of,hazard,net.m))).tolist()}
                     for t,hazard in enumerate(hazards)]}


def run_storm(net, peak_gust=160, hours=36, seed=1, storm_type='single_peak'):
    """Synthetic depression crossing the network south-west to north-east."""
    rng = np.random.default_rng(seed)
    samples = route_samples(net)
    centre, strength = storm_path(net, hours, rng)
    if storm_type=='persistent':
        strength=.35+.65*np.sin(np.pi*np.linspace(.05,.95,hours))**.35
    elif storm_type=='double_peak':
        strength=.25+.75*np.sin(2*np.pi*np.linspace(.025,.975,hours))**2
    elif storm_type!='single_peak':
        raise ValueError('Unknown storm preset')

    def gust_at(t):
        distance = np.sum((samples['xy']-centre[t])**2, axis=1)
        field = peak_gust*strength[t]*(.3+.7*np.exp(-distance/(2*STORM_SIGMA**2)))  # sea-level gust
        return field*rng.lognormal(0, GUSTINESS, field.shape)*samples['c_alt']

    marker = lambda t: ([round(float(c), 4) for c in centre[t]], STORM_SIGMA)
    frames, hazards = simulate_gusts(net, hours, gust_at, rng, marker)
    from datetime import datetime,timedelta,timezone
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    timestamps=[(start+timedelta(hours=t)).isoformat() for t in range(hours)]
    return {'config': {'peak_gust': peak_gust, 'hours': hours, 'seed': seed,'storm_type':storm_type}, 'model': model_info(net), 'frames': frames,
            'planning_weather':planning_exposure(net,frames,hazards,timestamps,'Synthetic geographic windstorm; illustrative route/pylon fragility')}
