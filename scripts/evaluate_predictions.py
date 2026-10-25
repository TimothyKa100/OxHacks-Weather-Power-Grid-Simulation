"""Evaluate verified held-out feeder probabilities; see docs/FAILURE_PIPELINE.md."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    import pandas as pd
    from failure_model.evaluation import evaluate, plot_calibration
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument("--alert-threshold", type=float, default=.01)
    args = parser.parse_args()
    report = evaluate(pd.read_parquet(args.predictions), args.output,
                      bootstrap_samples=args.bootstrap_samples, threshold=args.alert_threshold)
    plot_calibration(report, args.output.with_suffix(".png"))
    print(f"{report['rows']:,} verified hours; Brier={report['brier_score']:.6g}; log loss={report['log_loss']:.6g}")


if __name__ == "__main__":
    main()
