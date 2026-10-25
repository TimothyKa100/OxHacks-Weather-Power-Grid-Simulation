"""ML power-cut forecast: the corridor model's own prediction, compared with reported power cuts.

The corridor model (failure_model/gb_v2.py) was trained on SSEN fault reports: for each overhead
line and hour it gives the probability that a weather-caused distribution fault is reported within
5 km of the route in the next hour. Here that probability is the output itself, a forecast of where
local power cuts happen. It does not set transmission line failures; the cascade simulation stays
on the engineering hazard (optionally tilted by the experimental hybrid).

For a historical replay the forecast is scored against the storm's reported cuts in the same unit
as the target: a line-hour counts as observed when at least one cut was reported within 5 km of
the route in that hour. Only lines inside the outage feed's coverage and hours the model scores are
compared. One report near several lines counts for each, as in training.
"""
import numpy as np

from .ml_risk import ml_probabilities, overhead, training_domain

CORRIDOR_KM = 5.
COVERED_SHARE = .5  # a line is in the feed's coverage when half its route points lie in reporting cells


def _bng_routes(net):
    """Overhead routes as British National Grid LineStrings (None for other branches)."""
    import shapely
    from pyproj import Transformer
    from .benchmark import Projection
    proj = Projection(net)
    to_bng = Transformer.from_crs(4326, 27700, always_xy=True).transform
    flags = overhead(net)
    routes, lonlat = [], []
    for e, keep in zip(net.branches, flags):
        if not keep or len(e.get('geometry') or []) < 2:
            routes.append(None); lonlat.append(None); continue
        lon, lat = proj.lonlat(*np.asarray(e['geometry'], float).T)
        routes.append(shapely.LineString(np.column_stack(to_bng(lon, lat)))); lonlat.append((lon, lat))
    return routes, lonlat, to_bng


def _rounded(a, digits=6):
    return [[None if not np.isfinite(v) else round(float(v), digits) for v in row] for row in a]


def power_cut_forecast(net, exposure, storm=None, archive_storm=None):
    """Hourly corridor power-cut forecast for the run's network, plus predicted-vs-actual for real storms."""
    q, _, coverage, manifest, source, use_v2 = ml_probabilities(net, exposure, archive_storm)
    hours, m = q.shape
    valid = np.isfinite(q)
    p = np.where(valid, q, 0.)
    storm_probability = np.where(valid.any(axis=0), 1-np.prod(1-p, axis=0), np.nan)
    result = {'model_version': manifest['model_version'], 'source': source, 'feature_coverage': coverage['mode'],
              'target': 'P(weather-caused distribution fault reported within 5 km of the overhead route in the hour)',
              'corridor_km': CORRIDOR_KM, 'timestamps': [r['timestamp'] for r in exposure['hours']],
              'probability': _rounded(q), 'expected_per_hour': [round(float(v), 4) for v in p.sum(axis=1)],
              'hours_scored': int(valid.any(axis=1).sum()), 'lines_scored': int(valid.any(axis=0).sum()),
              'storm_probability': [None if not np.isfinite(v) else round(float(v), 6) for v in storm_probability],
              'in_training_domain': (training_domain(net.name) if use_v2 else np.zeros(m, bool)).tolist(),
              'scope': 'Local (distribution) power-cut risk near each overhead route; trained on SSEN fault reports, '
                       'mostly northern Scotland, so other regions are unvalidated extrapolation. Separate from the '
                       'transmission cascade simulation.'}
    if storm is not None:
        result['comparison'] = compare(net, q, storm)
    return result


def compare(net, q, storm):
    """Predicted vs reported cuts per line-hour inside the outage feed's coverage."""
    import shapely
    hours, m = q.shape
    routes, lonlat, to_bng = _bng_routes(net)
    size = storm['coverage_cell']; cells = {tuple(c) for c in storm['coverage']}
    covered = np.zeros(m, bool)
    for i, ll in enumerate(lonlat):
        if ll is None: continue
        inside = [(int(np.floor(a/size)), int(np.floor(b/size))) in cells for a, b in zip(*ll)]
        covered[i] = np.mean(inside) >= COVERED_SHARE
    lines = np.flatnonzero(covered)
    observed = np.zeros((hours, m))
    outages = [o for o in storm['outages'] if 0 <= int(o[2]) < hours]
    matched = 0
    if len(lines) and outages:
        tree = shapely.STRtree([routes[i] for i in lines])
        x, y = to_bng(np.array([o[0] for o in outages]), np.array([o[1] for o in outages]))
        report, hit = tree.query(shapely.points(x, y), predicate='dwithin', distance=CORRIDOR_KM*1000)
        hour = np.array([int(o[2]) for o in outages])
        observed[hour[report], lines[hit]] = 1
        matched = int(len(np.unique(report)))
    scored = np.isfinite(q) & covered[None, :]
    p = np.where(scored, q, 0.); y = np.where(scored, observed, 0.)
    expected_h, observed_h = p.sum(axis=1), y.sum(axis=1)
    per_line_expected, per_line_observed = p.sum(axis=0), y.sum(axis=0)
    # How many reported corridor-hours fall on the 10% of covered lines the model ranks riskiest.
    ranked = lines[np.argsort(-per_line_expected[lines], kind='stable')][:max(1, int(np.ceil(.1*len(lines))))] if len(lines) else []
    with np.errstate(invalid='ignore'):
        r = float(np.corrcoef(expected_h, observed_h)[0, 1]) if expected_h.std() > 0 and observed_h.std() > 0 else None
    return {'covered_lines': covered.tolist(), 'lines_compared': int(len(lines)), 'reports': len(storm['outages']),
            'reports_within_corridors': matched, 'operator': storm.get('operator'),
            'expected_per_hour': [round(float(v), 4) for v in expected_h], 'observed_per_hour': observed_h.astype(int).tolist(),
            'expected_total': round(float(expected_h.sum()), 2), 'observed_total': int(observed_h.sum()),
            'timing_correlation': None if r is None else round(r, 3),
            'peak_hour_expected': int(np.argmax(expected_h)), 'peak_hour_observed': int(np.argmax(observed_h)),
            'per_line_expected': [round(float(v), 4) if covered[i] else None for i, v in enumerate(per_line_expected)],
            'per_line_observed': [int(v) if covered[i] else None for i, v in enumerate(per_line_observed)],
            'top_decile_capture': round(float(y[:, ranked].sum()/max(y.sum(), 1)), 3) if len(lines) else None,
            'unit': 'Line-hours with at least one reported cut within 5 km of the route (the model target); '
                    'only lines in the feed coverage and hours the model scores'}
