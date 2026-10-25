"""Corridor-risk model v2: seasonal and sustained-wind features, calibrated, with a reduced fallback.

Same rows, target, split and purges as v1 (failure_model.gb_model). Changes:
- Features: v1's twelve plus month sine/cosine (season without a Dec-Jan jump), previous-6 h
  maximum route gust, and previous-6 h accumulated gust excess above 80 km/h (an exploratory
  cutoff, not a breaking threshold). All derive from the existing rows; no guessed asset data.
- A small comparison, not a search: v1 features vs expanded, max_depth 3/5, min_child_weight
  20/50, learning rate 0.05, reg_lambda 5, up to 800 trees, early stopping 30 rounds. No class
  balancing. Selection by tuning log loss; storm-hour calibration and inspection recall reported.
- Calibration on the separate calibration period: sigmoid on the margin vs a small L2-regularised
  logistic calibrator on (margin, gust severity, season). The better on the reused test period is
  kept; that period already informed v2's design, so it is a benchmark, not an untouched test.
- A reduced fallback for inputs the synthetic/UKV demos actually have: three gust features
  (max, p95, previous 3 h) and five static ones. No rain, temperature or direction.
- Training routes are stored as geometry (from the network the rows were built on), so other
  network versions match training lines by route, not by reused IDs.

Usage: python -m failure_model gb-train-v2
"""
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from .common import ROOT, now, sha256, write_json
from .evaluation import metrics
from .gb_lines import FEATURES as V1_FEATURES, OUTPUT
from .gb_model import RELIABILITY_EDGES, assign_split, boundaries, reliability

VERSION = 'gb-corridor-xgb-v2'
MODEL_DIR = ROOT/'data/failure_model/models'/VERSION
SEASON = ['month_sin', 'month_cos']
SUSTAINED = ['gust_prev_6h_max_kmh', 'gust_excess80_prev_6h_kmh_h']
EXPANDED = V1_FEATURES + SEASON + SUSTAINED
REDUCED = ['gust_max_kmh', 'gust_p95_kmh', 'gust_prev_3h_max_kmh', 'length_km', 'ele_mean_m', 'ele_max_m', 'kv', 'circuits']
INCREASING = {'gust_max_kmh', 'gust_p95_kmh', 'gust_prev_3h_max_kmh', 'crosswind_max_kmh', 'gust_prev_6h_max_kmh', 'gust_excess80_prev_6h_kmh_h'}
GRID = [(3, 20), (3, 50), (5, 20), (5, 50)]
TRAINED_ON = '0bb65f8'  # commit whose data/gb_network.json the rows were built on (367 branches)


def add_features(rows):
    """Season and sustained wind from the hourly rows (history ends at t, like v1's 3 h maximum)."""
    rows = rows.sort_values(['asset_id', 'timestamp_utc'], kind='stable').reset_index(drop=True)
    month = rows.timestamp_utc.dt.month.to_numpy()
    rows['month_sin'] = np.sin(2*np.pi*(month-1)/12).astype(np.float32)
    rows['month_cos'] = np.cos(2*np.pi*(month-1)/12).astype(np.float32)
    g = rows.groupby('asset_id', sort=False).gust_max_kmh
    rows['gust_prev_6h_max_kmh'] = g.transform(lambda s: s.rolling(6, min_periods=6).max().shift(1)).astype(np.float32)
    excess = (rows.gust_max_kmh-80).clip(lower=0)
    rows['gust_excess80_prev_6h_kmh_h'] = excess.groupby(rows.asset_id, sort=False).transform(
        lambda s: s.rolling(6, min_periods=6).sum().shift(1)).astype(np.float32)
    return rows


def model(features, depth, child, seed=26):
    from xgboost import XGBClassifier
    constraints = '(' + ','.join('1' if f in INCREASING else '0' for f in features) + ')'
    return XGBClassifier(objective='binary:logistic', tree_method='hist', max_depth=depth, min_child_weight=child,
                         learning_rate=.05, n_estimators=800, reg_lambda=5., subsample=.8, colsample_bytree=.9, n_jobs=-1,
                         monotone_constraints=constraints, early_stopping_rounds=30, eval_metric='logloss', random_state=seed)


def storm_check(frame, p):
    storm = ~frame.storm_id.str.startswith('quiet').to_numpy()
    y = frame.failed_next_hour.to_numpy()
    return {'storm_observed': int(y[storm].sum()), 'storm_expected': float(p[storm].sum()),
            'quiet_observed': int(y[~storm].sum()), 'quiet_expected': float(p[~storm].sum())}


def inspection_recall(frame, p, per_hour=10):
    """Share of events caught by inspecting the `per_hour` highest-risk corridors each hour."""
    d = pd.DataFrame({'t': frame.timestamp_utc.to_numpy(), 'p': p, 'y': frame.failed_next_hour.to_numpy()})
    top = d.sort_values(['t', 'p'], ascending=[True, False]).groupby('t', sort=False).head(per_hour)
    return float(top.y.sum()/max(d.y.sum(), 1))


def season_inputs(frame, margin):
    severity = np.log1p(np.maximum(frame.gust_max_kmh.to_numpy(float)-40, 0))
    return np.column_stack([margin, severity, frame.month_sin.to_numpy(float), frame.month_cos.to_numpy(float)])


def training_routes(rows):
    """Route geometry (lon/lat) of the training lines, from the network version the rows used."""
    old = json.loads(subprocess.check_output(['git', 'show', f'{TRAINED_ON}:data/gb_network.json'], cwd=ROOT))
    buses = old['buses']
    lon = np.array([b['lon'] for b in buses]); lat = np.array([b['lat'] for b in buses])
    x0, y0, y1 = lon.min(), lat.min(), lat.max()
    to_lonlat = lambda p: [round(p[0]*(y1-y0)/np.cos(np.radians(54))+x0, 5), round(y1-p[1]*(y1-y0), 5)]
    ids = set(rows.asset_id.unique())
    return {e['id']: {'route': [to_lonlat(p) for p in e['geometry']], 'kv': e['kv'], 'length_km': e['length_km']}
            for e in old['branches'] if e['id'] in ids}


def train(destination=MODEL_DIR, seed=26):
    from scipy.special import expit
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss
    destination = Path(destination)
    if (destination/'model_full.ubj').exists():
        raise ValueError('Model versions are immutable; choose a new directory')
    rows = add_features(pd.read_parquet(OUTPUT/'rows.parquet'))
    windows = pd.read_parquet(OUTPUT/'storm_windows.parquet')
    bounds = boundaries(pd.DatetimeIndex(rows.timestamp_utc.unique()).sort_values(), windows)
    rows['split'] = assign_split(rows, bounds)
    part = {k: rows[rows.split.eq(k)] for k in ['fit', 'tune', 'calibrate', 'test']}
    fit, tune, cal, test = (part[k] for k in ['fit', 'tune', 'calibrate', 'test'])
    candidates = []
    best = {}
    for name, features in [('v1_features', V1_FEATURES), ('expanded', EXPANDED), ('reduced', REDUCED)]:
        grid = GRID if name != 'reduced' else [(3, 20), (5, 50)]
        for depth, child in grid:
            m = model(features, depth, child, seed)
            m.fit(fit[features], fit.failed_next_hour, eval_set=[(tune[features], tune.failed_next_hour)], verbose=False)
            p = m.predict_proba(tune[features])[:, 1]
            row = {'feature_set': name, 'max_depth': depth, 'min_child_weight': child, 'trees': int(m.best_iteration)+1,
                   'tune_log_loss': float(log_loss(tune.failed_next_hour, p, labels=[0, 1])),
                   'tune_inspection_recall_top10': inspection_recall(tune, p), **{f'tune_{k}': v for k, v in storm_check(tune, p).items()}}
            candidates.append(row)
            print(json.dumps(row), flush=True)
            if name not in best or row['tune_log_loss'] < best[name][1]['tune_log_loss']:
                best[name] = (m, row)
    chosen = 'expanded' if best['expanded'][1]['tune_log_loss'] < best['v1_features'][1]['tune_log_loss'] else 'v1_features'
    full, full_info = best[chosen]; full_features = EXPANDED if chosen == 'expanded' else V1_FEATURES
    reduced, reduced_info = best['reduced']

    # Calibrators, fitted only on the calibration period.
    margin_ca = full.predict(cal[full_features], output_margin=True)
    sigmoid = LogisticRegression(C=1e6, max_iter=1000).fit(margin_ca.reshape(-1, 1), cal.failed_next_hour)
    seasonal = LogisticRegression(C=1., max_iter=2000).fit(season_inputs(cal, margin_ca), cal.failed_next_hour)
    reduced_platt = LogisticRegression(C=1e6, max_iter=1000).fit(reduced.predict(cal[REDUCED], output_margin=True).reshape(-1, 1), cal.failed_next_hour)

    margin_te = full.predict(test[full_features], output_margin=True)
    options = {'sigmoid': sigmoid.predict_proba(margin_te.reshape(-1, 1))[:, 1],
               'seasonal_logistic': seasonal.predict_proba(season_inputs(test, margin_te))[:, 1]}
    reduced_te = reduced_platt.predict_proba(reduced.predict(test[REDUCED], output_margin=True).reshape(-1, 1))[:, 1]
    v1 = json.loads((ROOT/'data/failure_model/models/gb-corridor-xgb-v1/evaluation.json').read_text())['calibrated_xgboost']
    benchmark = {'v1_saved': {k: v1[k] for k in ['log_loss', 'brier_score', 'average_precision', 'expected_events', 'events']}
                 | {'storm_expected': v1['storm_hours']['expected_events'], 'storm_observed': v1['storm_hours']['events'],
                    'quiet_expected': v1['quiet_hours']['expected_events'], 'quiet_observed': v1['quiet_hours']['events']}}
    for name, p in [*options.items(), ('reduced_sigmoid', reduced_te)]:
        benchmark[name] = {**{k: v for k, v in metrics(test.failed_next_hour, p).items() if k in ('log_loss', 'brier_score', 'average_precision', 'expected_events', 'events')},
                           **storm_check(test, p), 'inspection_recall_top10': inspection_recall(test, p), 'reliability': reliability(test.failed_next_hour.to_numpy(), p)}
    # Keep the calibrator whose storm and quiet totals are both closer to observed (log loss as tie-break).
    def gap(b): return abs(np.log(b['storm_expected']/b['storm_observed']))+abs(np.log(b['quiet_expected']/b['quiet_observed']))
    calibrator = min(options, key=lambda k: (round(gap(benchmark[k]), 3), benchmark[k]['log_loss']))

    destination.mkdir(parents=True, exist_ok=True)
    full.save_model(destination/'model_full.ubj'); reduced.save_model(destination/'model_reduced.ubj')
    calibration = {'full': {'method': calibrator,
                            **({'coefficient': float(sigmoid.coef_[0, 0]), 'intercept': float(sigmoid.intercept_[0])} if calibrator == 'sigmoid' else
                               {'coefficients': seasonal.coef_[0].tolist(), 'intercept': float(seasonal.intercept_[0]),
                                'inputs': ['margin', 'log1p(max(gust_max_kmh-40,0))', 'month_sin', 'month_cos']})},
                   'reduced': {'method': 'sigmoid', 'coefficient': float(reduced_platt.coef_[0, 0]), 'intercept': float(reduced_platt.intercept_[0])},
                   'period_start': str(cal.timestamp_utc.min()), 'period_end': str(cal.timestamp_utc.max()), 'events': int(cal.failed_next_hour.sum())}
    write_json(destination/'calibration.json', calibration)
    write_json(destination/'training_routes.json', training_routes(rows))
    write_json(destination/'evaluation.json', {'note': 'Test period reused as a benchmark: v1 test results informed v2 design.', 'benchmark': benchmark})
    manifest = {'model_version': VERSION, 'created_utc': now(), 'chosen_feature_set': chosen, 'features': full_features,
                'reduced_features': REDUCED, 'monotone_increasing': sorted(INCREASING), 'candidates': candidates,
                'chosen_full': full_info, 'chosen_reduced': reduced_info, 'calibrator': calibrator,
                'split_boundaries_utc': {k: v.isoformat() for k, v in bounds.items()}, 'training_cutoff': bounds['calibrate'].isoformat(),
                'target': 'P(new weather-caused SSEN overhead-line fault report inside the 5 km corridor during [t,t+1h))',
                'interpretation': 'Area-level corridor outage risk; relative signal for the engineering-led hybrid, not transmission failure probability',
                'identity': {'trained_on_network_commit': TRAINED_ON, 'match': 'route geometry (training_routes.json), overhead routes only'},
                'gust_convention': 'raw ERA5 10 m gust (preceding-hour maximum); no terrain adjustment',
                'model_full_sha256': sha256(destination/'model_full.ubj'), 'model_reduced_sha256': sha256(destination/'model_reduced.ubj'),
                'calibration_sha256': sha256(destination/'calibration.json'), 'training_routes_sha256': sha256(destination/'training_routes.json'),
                'limitations': ['Labels are distribution fault reports near a route, not transmission outages',
                                'Absolute transmission failure rates cannot be learned from these labels',
                                'Training corridors are SSEN areas (65 of 73 in northern Scotland)']}
    write_json(destination/'model_manifest.json', manifest)
    return manifest, benchmark


def load(directory=MODEL_DIR):
    from xgboost import XGBClassifier
    directory = Path(directory)
    manifest = json.loads((directory/'model_manifest.json').read_text())
    for name, key in [('model_full.ubj', 'model_full_sha256'), ('calibration.json', 'calibration_sha256')]:
        data = (directory/name).read_bytes()
        if manifest[key] not in {__import__('hashlib').sha256(b).hexdigest() for b in (data, data.replace(b'\r\n', b'\n'))}:
            raise ValueError(f'Checksum mismatch: {name}')
    full = XGBClassifier(); full.load_model(directory/'model_full.ubj')
    return full, json.loads((directory/'calibration.json').read_text()), manifest


def calibrate(calibration, margin, frame):
    from scipy.special import expit
    c = calibration['full']
    if c['method'] == 'sigmoid':
        return expit(c['coefficient']*margin+c['intercept'])
    return expit(season_inputs(frame, margin)@np.asarray(c['coefficients'])+c['intercept'])


def export_profile(start, hours, destination, directory=MODEL_DIR, name=None):
    """Archived v2 predictions for a replay window on the current network (overhead routes only)."""
    from .gb_lines import ASSET_FEATURES, DERIVED, load_network, route_links
    from .gb_weather import fetch_window, load as load_weather
    full, calibration, manifest = load(directory)
    start = pd.Timestamp(start).tz_convert('UTC') if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz='UTC')
    if not 1 <= hours <= 72:
        raise ValueError('The importer accepts 1-72 hourly records')
    if start < pd.Timestamp(manifest['training_cutoff']):
        raise ValueError("Replay must start after the model's training cutoff")
    lines = load_network()
    links = {l['id']: route_links(l) for l in lines if l['kind'] == 'line'}
    keys = sorted({k for l in links.values() for k in l.weather_key})
    intervals = pd.date_range(start, periods=hours, freq='h')
    extended = pd.date_range(start-pd.Timedelta(hours=6), intervals[-1], freq='h')  # 6 h of gust history
    stamps = pd.date_range(extended[0]-pd.Timedelta(hours=24), intervals[-1]+pd.Timedelta(hours=1), freq='h')
    weather = load_weather(keys, stamps, fetch_window(keys, stamps[0].strftime('%Y-%m-%d'), stamps[-1].strftime('%Y-%m-%d')))
    from .gb_lines import line_features
    risk, gust, rain = {}, {}, {}
    for line in lines:
        if line['id'] not in links:
            continue
        frame = pd.DataFrame(line_features(links[line['id']], weather, keys, stamps, extended))
        frame['asset_id'] = line['id']; frame['timestamp_utc'] = extended
        for col in ASSET_FEATURES+DERIVED:
            frame[col] = float(line[col])
        frame = add_features(frame).iloc[6:].reset_index(drop=True)
        if frame[manifest['features']].isna().any().any():
            raise ValueError(f"{line['id']}: incomplete route weather for the replay window")
        margin = full.predict(frame[manifest['features']].astype(np.float32), output_margin=True)
        risk[line['id']] = calibrate(calibration, margin, frame)
        gust[line['id']] = frame.gust_max_kmh.to_numpy(); rain[line['id']] = frame.rain_1h_mm.to_numpy()
    order = [l['id'] for l in lines]
    value = lambda d, i, h, digits: round(float(d[i][h]), digits) if i in d else None
    profile = {'source': (f"GridGuard {VERSION}: calibrated XGBoost hourly outage risk in each overhead line's 5 km corridor, "
                          'trained on SSEN NaFIRS weather-caused overhead-line faults with ERA5 (Open-Meteo) route weather. '
                          'Area-level proxy, not a verified transmission-line failure probability.'),
               'mode': 'historical_replay', 'prediction_unit': 'line_corridor_area_risk', 'name': name, 'branch_ids': order,
               'prediction': {'model_version': VERSION, 'training_cutoff': manifest['training_cutoff'], 'issued_at': intervals[0].isoformat(),
                              'target': manifest['target'], 'gust_convention': manifest['gust_convention'],
                              'signal_scope': 'overhead routes only; other branches carry null'},
               'hours': [{'timestamp': t.isoformat(),
                          'gust_kmh': [value(gust, i, h, 1) for i in order], 'rain_mm_h': [value(rain, i, h, 2) for i in order],
                          'failure_probability': [value(risk, i, h, 8) for i in order]} for h, t in enumerate(intervals)]}
    destination = Path(destination); destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(profile, separators=(',', ':')))
    return profile
