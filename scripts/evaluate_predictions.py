"""Evaluate verified held-out feeder probabilities; see docs/FAILURE_PIPELINE.md."""
import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("manifest", type=Path, nargs="?", help="CSV scoring model manifest")
    parser.add_argument("csv_output", type=Path, nargs="?", help="CSV score report path")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument("--alert-threshold", type=float, default=.01)
    args = parser.parse_args()
    if args.manifest is not None:
        output = args.csv_output or args.output
        if output is None: parser.error('CSV scoring requires an output path')
        try:
            with args.predictions.open(newline='') as stream:
                result = score(csv.DictReader(stream), json.loads(args.manifest.read_text()))
        except (ValueError, KeyError, TypeError) as exc: parser.error(str(exc))
        output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
        return
    if args.output is None: parser.error('Parquet evaluation requires --output')
    import pandas as pd
    from failure_model.evaluation import evaluate, plot_calibration
    report = evaluate(pd.read_parquet(args.predictions), args.output,
                      bootstrap_samples=args.bootstrap_samples, threshold=args.alert_threshold)
    plot_calibration(report, args.output.with_suffix(".png"))
    print(f"{report['rows']:,} verified hours; Brier={report['brier_score']:.6g}; log loss={report['log_loss']:.6g}")


if __name__ == "__main__":
    main()
