"""Use the shipped corridor model as a relative-risk signal, without new labels.

The reference is a same-hour median of predictions at comparable wind and
engineering risk. It is explicitly not a separately trained/calibrated model.
"""
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import numpy as np
from scipy.special import expit, logit
from .hybrid import instant, validate_pair

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / 'data/failure_model/models/gb-corridor-xgb-v1'
WEATHER_FEATURES = ('gust_max_kmh', 'gust_p95_kmh', 'gust_prev_3h_max_kmh',
                    'crosswind_max_kmh', 'rain_1h_mm', 'rain_prev_24h_mm', 'temperature_c')


@lru_cache(maxsize=1)
def saved_model():
    from xgboost import Booster
    manifest = json.loads((MODEL_DIR / 'model_manifest.json').read_text())
    binary = (MODEL_DIR / 'model.ubj').read_bytes()
    calibration = (MODEL_DIR / 'calibration.json').read_bytes()
    canonical = calibration.replace(b'\r\n', b'\n')
    hashes = {hashlib.sha256(b).hexdigest() for b in (calibration, canonical, canonical.replace(b'\n', b'\r\n'))}
    if (hashlib.sha256(binary).hexdigest() != manifest['model_sha256'] or
            manifest['calibration_sha256'] not in hashes):
        raise ValueError('Saved corridor model checksum mismatch')
    model = Booster(params={'nthread': 2})
    model.load_model(MODEL_DIR / 'model.ubj')
    return model, json.loads(calibration), manifest


def route_weather_features(net, raw_gusts):
    """Raw, unadjusted route gust summaries; unknown weather stays null, not zero.

    Synthetic samples have equal pylon shares within a line, so their empirical
    95th percentile uses equal weights. History excludes the current hour.
    """
    from .storm import route_samples
    line = route_samples(net)['line']
    groups = [np.flatnonzero(line == i) for i in range(net.m)]
    raw = np.asarray(raw_gusts)
    maxima = np.column_stack([raw[:, g].max(axis=1) for g in groups])
    p95 = np.column_stack([np.quantile(raw[:, g], .95, axis=1) for g in groups])
    return [dict(gust_max_kmh=maxima[t].tolist(), gust_p95_kmh=p95[t].tolist(),
                 gust_prev_3h_max_kmh=maxima[t-3:t].max(axis=0).tolist() if t >= 3 else None)
            for t in range(len(raw))]


def matched_reference(probability, gust, engineering, eligible_lines):
    """Leave-one-out peers: <=10 km/h gust difference and <=4x physical hazard.

    At least five peers, nearest twelve in scaled gust/log-hazard distance.
    Unsupported lines get a neutral correction. The candidate mask can exclude
    lines without usable inputs; geographic extrapolation is recorded separately.
    """
    q, gust, p = map(lambda a: np.asarray(a, float), (probability, gust, engineering))
    domain = np.asarray(eligible_lines, bool)
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


def builtin_pair(net, exposure, archive_storm=None):
    """Prefer complete archived predictions; otherwise infer from available features.

    No download or retraining. Missing weather follows the saved XGBoost missing
    branches and is reported, so partial-feature demos cannot claim calibration.
    """
    ids = [e['id'] for e in net.branches]
    dates = [instant(row['timestamp']) for row in exposure['hours']]
    manifest = json.loads((MODEL_DIR / 'model_manifest.json').read_text())
    if dates[0] < instant(manifest['training_cutoff']):
        raise ValueError('Built-in ML replay must follow its training cutoff')
    archive = None
    for path in sorted((ROOT / 'data/failure_model/exports').glob('*.json')):
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
        q = np.asarray([r['failure_probability'] for r in rows])
        gusts = np.asarray([r['gust_kmh'] for r in rows])
        source = profile['source']
        feature_coverage = {'mode': 'archived_complete_features', 'missing_features': []}
    else:
        if any('ml_weather' not in row for row in exposure['hours']):
            raise ValueError('Built-in hybrid needs raw route ml_weather features or matching archived predictions; terrain-adjusted gust is not a substitute')
        from xgboost import DMatrix
        model, calibration, manifest = saved_model()
        matrix, missing = [], set()
        for row in exposure['hours']:
            weather = row['ml_weather']
            values = {}
            for key in WEATHER_FEATURES:
                a = np.broadcast_to(np.asarray(weather.get(key), float), (net.m,))
                if np.any(np.isinf(a)):
                    raise ValueError(f'Invalid ml_weather {key}')
                values[key] = a
                if np.isnan(a).any():
                    missing.add(key)
            if np.any(~np.isfinite(values['gust_max_kmh'])) or np.any(values['gust_max_kmh'] < 0):
                raise ValueError('Built-in hybrid requires finite raw gust_max_kmh for every branch')
            for i, branch in enumerate(net.branches):
                matrix.append([values[f][i] if f in WEATHER_FEATURES else branch[f] for f in manifest['features']])
        best = model.attr('best_iteration')
        margin = model.predict(DMatrix(np.asarray(matrix, np.float32), feature_names=manifest['features']),
                               output_margin=True, iteration_range=(0, int(best)+1) if best is not None else (0, 0))
        q = expit(calibration['coefficient']*margin+calibration['intercept']).reshape(len(dates), net.m)
        gusts = np.asarray([np.broadcast_to(row['ml_weather']['gust_max_kmh'], (net.m,)) for row in exposure['hours']])
        source = 'Saved calibrated XGBoost on available raw route weather; missing inputs follow tree missing-value paths'
        feature_coverage = {'mode': 'partial_feature_inference' if missing else 'complete_feature_inference',
                            'missing_features': sorted(missing),
                            'weather_convention': exposure.get('ml_weather_source', 'User-supplied raw route features')}
    domain = [i in manifest['training_domain_lines'] for i in ids]
    pair = {'source': source, 'prediction_unit': 'paired_risk_modifier', 'branch_ids': ids,
            'prediction': {'model_version': manifest['model_version'], 'target': 'corridor_outage',
                'reference_model_version': 'same-hour-weather-and-engineering-peers-v1',
                'reference_method': 'Leave-one-out median log odds; up to 12 GB peers, at least 5; gust ±10 km/h, physical hazard within 4x',
                'integration_method': 'relative-risk-budget-v1', 'feature_coverage': feature_coverage,
                'training_cutoff': manifest['training_cutoff'], 'issued_at': dates[0].isoformat(),
                'evidence_status': 'observed_labels', 'in_training_domain': domain,
                'evidence_scope': 'Observed distribution-corridor labels; GB geographic transfer, transmission transfer and missing-feature inference are unvalidated'},
            'hours': []}
    for t, row in enumerate(exposure['hours']):
        reference, support = matched_reference(q[t], gusts[t], row['failure_probability'], np.ones(net.m,bool))
        pair['hours'].append({'timestamp': row['timestamp'], 'full_probability': q[t].tolist(),
                              'reference_probability': reference.tolist(), 'reference_peer_count': support.tolist()})
    validate_pair(pair, ids, [r['timestamp'] for r in exposure['hours']])
    return pair
