"""Hyperparameter search and SHAP importance for the GB line-corridor XGBoost model.

Uses the same rows, split, features, monotone constraints and early stopping as
failure_model.gb_model.train. Each candidate is chosen on the tune period only. The winner is
Platt-calibrated on the calibration period and scored once on the untouched test period. SHAP
values (exact TreeSHAP via XGBoost's pred_contribs) are computed on a sample of test rows. The
saved v1 model is not changed.

Usage: python scripts/tune_corridor_model.py [--output data/failure_model/gb_corridor/tuning.json]
"""
import argparse
import itertools
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from failure_model.evaluation import metrics
from failure_model.gb_lines import FEATURES, OUTPUT
from failure_model.gb_model import INCREASING, assign_split, boundaries, load_model

DEPTHS = [5, 7, 9, 12]
L1 = [0., 1., 5.]           # reg_alpha
L2 = [1., 5., 20.]          # reg_lambda
MIN_CHILD = 50


def model(depth, alpha, lam, seed=26):
    from xgboost import XGBClassifier
    constraints = "(" + ",".join("1" if f in INCREASING else "0" for f in FEATURES) + ")"
    return XGBClassifier(objective="binary:logistic", tree_method="hist", max_depth=depth, min_child_weight=MIN_CHILD,
                         learning_rate=.05, n_estimators=1500, reg_alpha=alpha, reg_lambda=lam, subsample=.8,
                         colsample_bytree=.9, n_jobs=-1, monotone_constraints=constraints, early_stopping_rounds=50,
                         eval_metric="logloss", random_state=seed)


def main():
    from scipy.special import expit
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--output', type=Path, default=OUTPUT/'tuning.json')
    args = parser.parse_args()
    rows = pd.read_parquet(OUTPUT/'rows.parquet', columns=['asset_id', 'timestamp_utc', 'storm_id', 'failed_next_hour', *FEATURES])
    windows = pd.read_parquet(OUTPUT/'storm_windows.parquet')
    rows['split'] = assign_split(rows, boundaries(pd.DatetimeIndex(rows.timestamp_utc.unique()).sort_values(), windows))
    part = {k: rows[rows.split.eq(k)] for k in ['fit', 'tune', 'calibrate', 'test']}
    fit, tune = part['fit'], part['tune']
    results = []
    for depth, alpha, lam in itertools.product(DEPTHS, L1, L2):
        started = time.time()
        m = model(depth, alpha, lam)
        m.fit(fit[FEATURES], fit.failed_next_hour, eval_set=[(tune[FEATURES], tune.failed_next_hour)], verbose=False)
        loss = float(log_loss(tune.failed_next_hour, m.predict_proba(tune[FEATURES])[:, 1], labels=[0, 1]))
        results.append({'max_depth': depth, 'reg_alpha': alpha, 'reg_lambda': lam, 'trees': int(m.best_iteration)+1,
                        'tune_log_loss': loss, 'seconds': round(time.time()-started)})
        print(json.dumps(results[-1]), flush=True)
    best = min(results, key=lambda r: r['tune_log_loss'])
    m = model(best['max_depth'], best['reg_alpha'], best['reg_lambda'])
    m.fit(fit[FEATURES], fit.failed_next_hour, eval_set=[(tune[FEATURES], tune.failed_next_hour)], verbose=False)
    ca, te = part['calibrate'], part['test']
    platt = LogisticRegression(C=1e6, max_iter=1000).fit(m.predict(ca[FEATURES], output_margin=True).reshape(-1, 1), ca.failed_next_hour)
    calibrate = lambda frame: platt.predict_proba(m.predict(frame[FEATURES], output_margin=True).reshape(-1, 1))[:, 1]
    v1, cal1, manifest = load_model()
    v1_p = expit(cal1['coefficient']*v1.predict(te[FEATURES], output_margin=True)+cal1['intercept'])
    storm = ~te.storm_id.str.startswith('quiet').to_numpy()
    p = calibrate(te)
    test = {'v1 (depth 5, L2 5)': metrics(te.failed_next_hour, v1_p), 'best of search': metrics(te.failed_next_hour, p),
            'best of search, storm hours': metrics(te.failed_next_hour[storm], p[storm]),
            'v1, storm hours': metrics(te.failed_next_hour[storm], v1_p[storm])}
    # SHAP on a test sample: all events plus 200k non-events (mean |SHAP| in log-odds).
    rng = np.random.default_rng(26)
    idx = np.concatenate([np.flatnonzero(te.failed_next_hour.to_numpy() == 1),
                          rng.choice(np.flatnonzero(te.failed_next_hour.to_numpy() == 0), 200000, replace=False)])
    sample = te.iloc[idx]
    shap = {}
    for name, booster in [('v1', v1), ('best of search', m)]:
        contrib = booster.get_booster().predict(__import__('xgboost').DMatrix(sample[FEATURES]), pred_contribs=True)[:, :-1]
        events = sample.failed_next_hour.to_numpy() == 1
        shap[name] = {f: {'mean_abs_all': float(np.abs(contrib[:, k]).mean()), 'mean_abs_events': float(np.abs(contrib[events, k]).mean()),
                          'mean_events': float(contrib[events, k].mean())} for k, f in enumerate(FEATURES)}
    out = {'grid': {'max_depth': DEPTHS, 'reg_alpha': L1, 'reg_lambda': L2, 'min_child_weight': MIN_CHILD},
           'candidates': results, 'best': best, 'test': test, 'shap_log_odds': shap,
           'note': 'Selection on tune log loss only; test scored once for the winner and for saved v1.'}
    args.output.write_text(json.dumps(out, indent=2))
    print(json.dumps({'best': best, 'test': {k: {x: round(v[x], 6) for x in ['log_loss', 'brier_score', 'average_precision', 'events', 'expected_events']} for k, v in test.items()}}, indent=1))


if __name__ == '__main__':
    main()
