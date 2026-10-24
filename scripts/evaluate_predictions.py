"""Score real incident predictions on storms excluded from model fitting.

CSV: storm_id,timestamp,asset_id,probability,failed (0 or 1).
Manifest: model_version, training_cutoff (ISO with timezone), training_storms,
baseline_failure_probability (estimated on training data), labels_source.
This scores submitted predictions; it does not train or calibrate a model.
"""
import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path


def score(rows, manifest):
    required = ('model_version', 'training_cutoff', 'training_storms', 'baseline_failure_probability', 'labels_source')
    if any(k not in manifest for k in required): raise ValueError('Incomplete model manifest')
    cutoff = datetime.fromisoformat(manifest['training_cutoff'].replace('Z', '+00:00'))
    if cutoff.tzinfo is None: raise ValueError('Training cutoff requires a timezone')
    if not isinstance(manifest['training_storms'], list) or not manifest['labels_source']:
        raise ValueError('Supply training storm IDs and the observed label source')
    baseline = float(manifest['baseline_failure_probability'])
    if not math.isfinite(baseline) or not 0 <= baseline <= 1: raise ValueError('Invalid training baseline')
    seen = set(); values = []; storms = set()
    for row in rows:
        storm = row['storm_id']
        if not storm or storm in manifest['training_storms']: raise ValueError('Evaluation storms must be excluded from training')
        stamp = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp <= cutoff: raise ValueError('Evaluation outcomes must follow training cutoff')
        key = (storm, stamp, row['asset_id'])
        if key in seen or not row['asset_id']: raise ValueError('Duplicate or missing asset-hour identity')
        seen.add(key); storms.add(storm)
        p = float(row['probability'])
        if not math.isfinite(p) or not 0 <= p <= 1 or str(row['failed']) not in ('0', '1'):
            raise ValueError('Expected finite probability in [0,1] and failed=0/1')
        values.append((p, int(row['failed'])))
    if not values: raise ValueError('No evaluation outcomes')
    n = len(values)
    brier = sum((p-y)**2 for p, y in values)/n
    base_brier = sum((baseline-y)**2 for _, y in values)/n
    log_loss = -sum(y*math.log(max(1e-15,p))+(1-y)*math.log(max(1e-15,1-p)) for p,y in values)/n
    calibration = []
    for i in range(10):
        bucket = [(p,y) for p,y in values if min(9,int(p*10))==i]
        if bucket:
            calibration.append(dict(lower=i/10,upper=(i+1)/10,count=len(bucket),
                                    predicted_mean=sum(p for p,_ in bucket)/len(bucket),
                                    observed_frequency=sum(y for _,y in bucket)/len(bucket)))
    return dict(model_version=manifest['model_version'], labels_source=manifest['labels_source'],
                held_out_storms=sorted(storms), asset_hours=n, failures=sum(y for _,y in values),
                brier_score=brier, log_loss=log_loss, training_baseline_brier=base_brier,
                brier_skill=1-brier/base_brier if base_brier else None, calibration=calibration,
                limits='Scores require observed asset failures and operating non-event hours. Manifest checks cannot detect leakage inside model features. No simulated outcomes establish real-world accuracy.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv'); parser.add_argument('manifest'); parser.add_argument('output')
    args = parser.parse_args()
    try:
        with open(args.csv,newline='') as stream:
            result=score(csv.DictReader(stream),json.loads(Path(args.manifest).read_text()))
    except (ValueError, KeyError, TypeError) as exc: parser.error(str(exc))
    Path(args.output).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
