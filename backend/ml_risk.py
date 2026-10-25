"""Use the corridor-risk model as a relative-risk signal, without new labels.

The reference is a same-hour median of predictions at comparable wind and
engineering risk. It is explicitly not a separately trained/calibrated model.

Model v2 (failure_model/gb_v2.py) is used when present: its full model needs the
ERA5-style route inputs (gust, crosswind, rain, temperature, season, 6 h history);
demos and UKV replays only have route gusts, so they use v2's reduced model
(three gust features and five static ones) rather than missing-value paths.
Only overhead routes carry the wind-damage signal: cables, transformers and
inferred links stay at their engineering hazard. Hours without three hours of
gust history stay engineering-only. Training-domain membership is matched by
route geometry, because branch IDs are reused between network versions.
"""
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import numpy as np
from scipy.special import expit, logit
from .hybrid import instant, validate_pair
from .paths import DATA

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = DATA / 'failure_model/models/gb-corridor-xgb-v1'
V2_DIR = DATA / 'failure_model/models/gb-corridor-xgb-v2'
WEATHER_FEATURES = ('gust_max_kmh', 'gust_p95_kmh', 'gust_prev_3h_max_kmh',
                    'crosswind_max_kmh', 'rain_1h_mm', 'rain_prev_24h_mm', 'temperature_c')
V2_WEATHER = WEATHER_FEATURES + ('gust_prev_6h_max_kmh', 'gust_excess80_prev_6h_kmh_h')
MATCH_KM = 2.0       # a route point within this distance of a training route counts as covered
MATCH_SHARE = .8     # share of a branch's route that must be covered to be in the training domain


def _checked(path, expected):
    data = path.read_bytes()
    canonical = data.replace(b'\r\n', b'\n')
    if expected not in {hashlib.sha256(b).hexdigest() for b in (data, canonical, canonical.replace(b'\n', b'\r\n'))}:
        raise ValueError(f'Checksum mismatch: {path.name}')
    return data


@lru_cache(maxsize=1)
def saved_model():
    """Model v1 (kept for comparison and its archived exports)."""
    from xgboost import Booster
    manifest = json.loads((MODEL_DIR / 'model_manifest.json').read_text())
    _checked(MODEL_DIR / 'model.ubj', manifest['model_sha256'])
    calibration = _checked(MODEL_DIR / 'calibration.json', manifest['calibration_sha256'])
    model = Booster(params={'nthread': 2})
    model.load_model(MODEL_DIR / 'model.ubj')
    return model, json.loads(calibration), manifest


@lru_cache(maxsize=1)
def v2_model():
    """Full and reduced v2 boosters with calibration and training routes, checksum-verified."""
    from xgboost import Booster
    manifest = json.loads((V2_DIR / 'model_manifest.json').read_text())
    boosters = {}
    for kind in ('full', 'reduced'):
        path = V2_DIR / f'model_{kind}.ubj'
        _checked(path, manifest[f'model_{kind}_sha256'])
        boosters[kind] = Booster(params={'nthread': 2}); boosters[kind].load_model(path)
    calibration = json.loads(_checked(V2_DIR / 'calibration.json', manifest['calibration_sha256']))
    routes = json.loads(_checked(V2_DIR / 'training_routes.json', manifest['training_routes_sha256']))
    return boosters, calibration, routes, manifest


def overhead(net):
    return np.array([e.get('kind', 'line') == 'line' and bool(e.get('samples')) for e in net.branches])


def route_weather_features(net, raw_gusts):
    """Raw, unadjusted route gust summaries; unknown weather stays null, not zero.

    Synthetic samples have equal pylon shares within a line, so their empirical
    95th percentile uses equal weights. History excludes the current hour.
    Branches without route samples (cables, transformers, links) get nulls.
    """
    from .storm import route_samples
    line = route_samples(net)['line']
    groups = [np.flatnonzero(line == i) for i in range(net.m)]
    raw = np.asarray(raw_gusts)
    empty = np.full(len(raw), np.nan)
    maxima = np.column_stack([raw[:, g].max(axis=1) if len(g) else empty for g in groups])
    p95 = np.column_stack([np.quantile(raw[:, g], .95, axis=1) if len(g) else empty for g in groups])
    clean = lambda a: [None if not np.isfinite(v) else float(v) for v in a]
    excess = np.maximum(maxima-80, 0)
    return [dict(gust_max_kmh=clean(maxima[t]), gust_p95_kmh=clean(p95[t]),
                 gust_prev_3h_max_kmh=clean(maxima[t-3:t].max(axis=0)) if t >= 3 else None,
                 gust_prev_6h_max_kmh=clean(maxima[t-6:t].max(axis=0)) if t >= 6 else None,
                 gust_excess80_prev_6h_kmh_h=clean(excess[t-6:t].sum(axis=0)) if t >= 6 else None)
            for t in range(len(raw))]


@lru_cache(maxsize=8)
def training_domain(name):
    """Overhead branches of network `name` whose route lies along a training route (same voltage)."""
    import shapely
    from pyproj import Transformer
    from .benchmark import Projection
    from .simulation import Network
    net = Network(name)
    _, _, routes, _ = v2_model()
    to_bng = Transformer.from_crs(4326, 27700, always_xy=True).transform
    lines = {kv: shapely.union_all([shapely.LineString([to_bng(*p) for p in r['route']]) for r in routes.values() if r['kv'] == kv])
             for kv in {r['kv'] for r in routes.values()}}
    proj = Projection(net); flags = overhead(net)
    domain = np.zeros(net.m, bool)
    for i, e in enumerate(net.branches):
        if not flags[i] or e['kv'] not in lines: continue
        lon, lat = proj.lonlat(*np.asarray(e['geometry'], float).T)
        points = shapely.points(*to_bng(lon, lat))
        domain[i] = (shapely.distance(points, lines[e['kv']]) <= MATCH_KM*1000).mean() >= MATCH_SHARE
    return domain


def matched_reference(probability, gust, engineering, eligible_lines):
    """Leave-one-out peers: <=10 km/h gust difference and <=4x physical hazard.

    At least five peers, nearest twelve in scaled gust/log-hazard distance.
    Unsupported lines get a neutral correction. The candidate mask can exclude
    lines without usable inputs; geographic extrapolation is recorded separately.
    """
    q, gust, p = map(lambda a: np.asarray(a, float), (probability, gust, engineering))
    domain = np.asarray(eligible_lines, bool) & np.isfinite(gust) & np.isfinite(q)
    h = -np.log1p(-np.minimum(p, 1-1e-12))
    logh = np.log(np.maximum(h, 1e-300))
    reference, support = q.copy(), np.zeros(len(q), int)
    odds = logit(np.clip(q, 1e-12, 1-1e-12))
    for i in np.flatnonzero(domain & (p > 0) & (p < 1)):
        wind_delta = np.abs(gust-gust[i]) / 10
        hazard_delta = np.abs(logh-logh[i]) / np.log(4)
        eligible = domain & (p > 0) & (p < 1) & (wind_delta <= 1) & (hazard_delta <= 1)
        eligible[i] = False
        peers = np.flatnonzero(eligible)
        if len(peers) < 5:
            continue
        peers = peers[np.argsort((wind_delta**2+hazard_delta**2)[peers], kind='stable')[:12]]
        reference[i] = expit(np.median(odds[peers]))
        support[i] = len(peers)
    return reference, support


def _season(dates):
    month = np.array([d.month for d in dates])
    return np.sin(2*np.pi*(month-1)/12), np.cos(2*np.pi*(month-1)/12)


def v2_inference(net, exposure, dates):
    """Calibrated v2 probabilities (hours x branches; NaN where the signal does not apply) and coverage."""
    from xgboost import DMatrix
    boosters, calibration, _, manifest = v2_model()
    flags = overhead(net)
    rows = exposure['hours']
    weather = [row['ml_weather'] for row in rows]
    available = {k for k in V2_WEATHER if all(w.get(k) is not None for w in weather[6:] or weather)}
    complete = set(manifest['features']) & set(V2_WEATHER) <= available
    kind = 'full' if complete else 'reduced'
    features = manifest['features'] if complete else manifest['reduced_features']
    sin, cos = _season(dates)
    q = np.full((len(rows), net.m), np.nan)
    statics = {f: np.array([float(b.get(f) or 0.) for b in net.branches]) for f in ('length_km', 'ele_mean_m', 'ele_max_m', 'kv', 'circuits')}
    for t, w in enumerate(weather):
        values = {f: np.broadcast_to(np.asarray([np.nan if v is None else v for v in np.atleast_1d(w[f])] if w.get(f) is not None else np.nan, float), (net.m,))
                  for f in V2_WEATHER if f in w or f in features}
        needed = [f for f in features if f in V2_WEATHER]
        ready = flags & np.all([np.isfinite(values[f]) if f in values else np.zeros(net.m, bool) for f in needed], axis=0)
        if not ready.any(): continue  # not enough gust history yet: engineering alone
        columns = []
        for f in features:
            if f in statics: columns.append(statics[f])
            elif f == 'month_sin': columns.append(np.full(net.m, sin[t]))
            elif f == 'month_cos': columns.append(np.full(net.m, cos[t]))
            else: columns.append(values[f])
        x = np.column_stack(columns)[ready].astype(np.float32)
        best = boosters[kind].attr('best_iteration')  # saved boosters keep the early-stopping overshoot
        margin = boosters[kind].predict(DMatrix(x, feature_names=list(features)), output_margin=True,
                                        iteration_range=(0, int(best)+1) if best is not None else (0, 0))
        c = calibration[kind]
        if c['method'] == 'sigmoid':
            z = c['coefficient']*margin+c['intercept']
        else:
            gust = values['gust_max_kmh'][ready]
            inputs = np.column_stack([margin, np.log1p(np.maximum(gust-40, 0)), np.full(len(margin), sin[t]), np.full(len(margin), cos[t])])
            z = inputs@np.asarray(c['coefficients'])+c['intercept']
        q[t, np.flatnonzero(ready)] = expit(z)
    coverage = {'mode': f'v2_{kind}_model', 'model': kind, 'features': list(features),
                'missing_for_full_model': sorted(set(manifest['features']) & set(V2_WEATHER)-available),
                'weather_convention': exposure.get('ml_weather_source', 'User-supplied raw route features')}
    return q, coverage, manifest


def ml_probabilities(net, exposure, archive_storm=None):
    """Calibrated corridor-model probabilities, hours x branches (NaN where the model does not apply).

    Prefers complete archived predictions of the current model for a named replay; otherwise infers
    from the exposure's raw route weather (v2 reduced model when only gusts exist). No download or
    retraining. Non-overhead branches and hours without gust history are NaN.
    Returns q, route gusts, feature coverage, manifest, source text and whether v2 was used.
    """
    ids = [e['id'] for e in net.branches]
    dates = [instant(row['timestamp']) for row in exposure['hours']]
    use_v2 = (V2_DIR / 'model_manifest.json').exists()
    manifest = json.loads(((V2_DIR if use_v2 else MODEL_DIR) / 'model_manifest.json').read_text())
    if dates[0] < instant(manifest['training_cutoff']):
        raise ValueError('Built-in ML replay must follow its training cutoff')
    flags = overhead(net)
    archive = None
    for path in sorted((DATA / 'failure_model/exports').glob('*.json')):
        if archive_storm is None or not path.stem.startswith(archive_storm+'_'):
            continue
        candidate = json.loads(path.read_text())
        if (candidate.get('prediction_unit') != 'line_corridor_area_risk' or candidate.get('branch_ids') != ids or
                candidate.get('prediction', {}).get('model_version') != manifest['model_version']):
            continue
        rows = {instant(r['timestamp']): r for r in candidate['hours']}
        if all(t in rows for t in dates):
            archive = candidate, [rows[t] for t in dates]
            break
    if archive:
        profile, rows = archive
        number = lambda values: np.array([np.nan if v is None else v for v in values], float)
        q = np.where(flags, [number(r['failure_probability']) for r in rows], np.nan)
        gusts = np.array([number(r['gust_kmh']) for r in rows])
        source = profile['source']
        feature_coverage = {'mode': 'archived_complete_features', 'missing_features': []}
    else:
        if any('ml_weather' not in row for row in exposure['hours']):
            raise ValueError('The corridor model needs raw route ml_weather features or matching archived predictions; terrain-adjusted gust is not a substitute')
        gusts = np.asarray([[np.nan if v is None else v for v in np.broadcast_to(np.asarray(row['ml_weather']['gust_max_kmh'], object), (net.m,))]
                            for row in exposure['hours']], float)
        if np.any(gusts[:, flags] < 0) or not np.all(np.isfinite(gusts[:, flags])):
            raise ValueError('The corridor model requires finite raw gust_max_kmh for every overhead route')
        if use_v2:
            q, feature_coverage, manifest = v2_inference(net, exposure, dates)
            source = f"Calibrated {manifest['model_version']} ({feature_coverage['model']} model) on raw route weather; overhead routes only"
        else:
            q, feature_coverage, manifest = _v1_inference(net, exposure, dates, flags)
            source = 'Saved calibrated XGBoost on available raw route weather; missing inputs follow tree missing-value paths'
    return q, gusts, feature_coverage, manifest, source, use_v2


def builtin_pair(net, exposure, archive_storm=None):
    """Experimental hybrid input: the corridor model as a relative signal against same-hour peers."""
    ids = [e['id'] for e in net.branches]
    dates = [instant(row['timestamp']) for row in exposure['hours']]
    q, gusts, feature_coverage, manifest, source, use_v2 = ml_probabilities(net, exposure, archive_storm)
    domain = training_domain(net.name).tolist() if use_v2 else [i in manifest['training_domain_lines'] for i in ids]
    pair = {'source': source, 'prediction_unit': 'paired_risk_modifier', 'branch_ids': ids,
            'prediction': {'model_version': manifest['model_version'], 'target': 'corridor_outage',
                'reference_model_version': 'same-hour-weather-and-engineering-peers-v1',
                'reference_method': 'Leave-one-out median log odds; up to 12 GB peers, at least 5; gust ±10 km/h, physical hazard within 4x',
                'integration_method': 'relative-risk-budget-v1', 'feature_coverage': feature_coverage,
                'training_cutoff': manifest['training_cutoff'], 'issued_at': dates[0].isoformat(),
                'evidence_status': 'observed_labels', 'in_training_domain': domain,
                'signal_scope': 'Overhead routes only; cables, transformers, inferred links and hours without gust history keep the engineering hazard',
                'evidence_scope': 'Observed distribution-corridor labels; GB geographic transfer, transmission transfer and missing-feature inference are unvalidated'},
            'hours': []}
    for t, row in enumerate(exposure['hours']):
        valid = np.isfinite(q[t])
        probability = np.where(valid, q[t], np.asarray(row['failure_probability'], float))  # neutral where no signal
        reference, support = matched_reference(np.where(valid, q[t], np.nan), gusts[t], row['failure_probability'], valid)
        reference = np.where(valid, reference, probability)
        pair['hours'].append({'timestamp': row['timestamp'], 'full_probability': np.clip(probability, 0, 1).tolist(),
                              'reference_probability': np.clip(reference, 0, 1).tolist(), 'reference_peer_count': np.where(valid, support, 0).tolist()})
    validate_pair(pair, ids, [r['timestamp'] for r in exposure['hours']])
    return pair


def _v1_inference(net, exposure, dates, flags):
    from xgboost import DMatrix
    model, calibration, manifest = saved_model()
    matrix, missing = [], set()
    for row in exposure['hours']:
        weather = row['ml_weather']
        values = {}
        for key in WEATHER_FEATURES:
            raw = weather.get(key)
            a = np.broadcast_to(np.asarray([np.nan if v is None else v for v in np.atleast_1d(raw)] if raw is not None else np.nan, float), (net.m,))
            values[key] = a
            if np.isnan(a[flags]).any(): missing.add(key)
        for i, branch in enumerate(net.branches):
            matrix.append([values[f][i] if f in WEATHER_FEATURES else branch[f] for f in manifest['features']])
    best = model.attr('best_iteration')
    margin = model.predict(DMatrix(np.asarray(matrix, np.float32), feature_names=manifest['features']),
                           output_margin=True, iteration_range=(0, int(best)+1) if best is not None else (0, 0))
    q = expit(calibration['coefficient']*margin+calibration['intercept']).reshape(len(dates), net.m)
    q[:, ~flags] = np.nan
    return q, {'mode': 'partial_feature_inference' if missing else 'complete_feature_inference', 'missing_features': sorted(missing),
               'weather_convention': exposure.get('ml_weather_source', 'User-supplied raw route features')}, manifest
