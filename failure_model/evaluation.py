"""Probability evaluation on real-prevalence held-out exposure."""
import numpy as np
import pandas as pd

from .common import require_columns, write_json


def metrics(target, probability, threshold=.01):
    from sklearn.metrics import average_precision_score, brier_score_loss, log_loss
    y, p = np.asarray(target), np.asarray(probability, dtype=float)
    if not len(y) or not np.isin(y, [0, 1]).all():
        raise ValueError("Evaluation requires nonempty binary verified outcomes")
    if p.shape != y.shape or not np.isfinite(p).all() or not ((p >= 0) & (p <= 1)).all():
        raise ValueError("Probabilities must be finite, aligned and in [0,1]")
    if not 0 <= threshold <= 1:
        raise ValueError("Alert threshold must be in [0,1]")
    return {"rows": len(y), "events": int(y.sum()), "event_frequency": float(y.mean()),
            "expected_events": float(p.sum()), "brier_score": float(brier_score_loss(y, p)),
            "log_loss": float(log_loss(y, p, labels=[0, 1])),
            "average_precision": float(average_precision_score(y, p)) if y.sum() else None,
            "alert_threshold": threshold,
            "false_alerts_per_1000_asset_hours": float(((p >= threshold) & (y == 0)).mean() * 1000),
            "recall_at_threshold": float(((p >= threshold) & (y == 1)).sum() / y.sum()) if y.sum() else None}


def evaluate(frame, destination=None, bootstrap_samples=1000, seed=26, threshold=.01):
    require_columns(frame, ["asset_id", "cutoff_utc", "storm_id", "target", "probability",
                            "at_risk_verified", "label_verified"], "predictions")
    if frame.duplicated(["asset_id", "cutoff_utc"]).any():
        raise ValueError("Duplicate asset-hour predictions")
    if not isinstance(frame.cutoff_utc.dtype, pd.DatetimeTZDtype) or str(frame.cutoff_utc.dt.tz) != "UTC" or frame.cutoff_utc.isna().any():
        raise ValueError("Evaluation cutoffs must be non-null UTC timestamps")
    if not frame.at_risk_verified.eq(True).all() or not frame.label_verified.eq(True).all():
        raise ValueError("Unverified or censored exposure cannot be evaluated as labelled data")
    if frame.storm_id.isna().any():
        raise ValueError("Every evaluation row needs a storm or quiet-period group")
    if "sampling_weight" in frame and not frame.sampling_weight.eq(1).all():
        raise ValueError("Evaluation must retain full exposure, not sampled negatives")
    report = metrics(frame.target, frame.probability, threshold)
    p = frame.probability.to_numpy(float)
    # Fixed bins preserve the rare-event probability scale; empty bins stay empty.
    edges = np.array([0, .0001, .0003, .001, .003, .01, .03, .1, .3, 1.000001])
    bins = np.digitize(p, edges[1:-1])
    report["reliability"] = []
    for i in range(len(edges) - 1):
        subset = frame[bins == i]
        if len(subset):
            report["reliability"].append({"lower": float(edges[i]), "upper": float(min(edges[i + 1], 1)),
                                           "rows": len(subset), "mean_probability": float(subset.probability.mean()),
                                           "observed_frequency": float(subset.target.mean())})
    grouped = frame.assign(brier_error=(frame.probability - frame.target.astype(float)) ** 2).groupby("storm_id").agg(
        rows=("target", "size"), events=("target", "sum"), expected_events=("probability", "sum"),
        brier_sum=("brier_error", "sum"))
    report["storm_totals"] = grouped.reset_index().to_dict("records")
    rng = np.random.default_rng(seed)
    if len(grouped) > 1 and bootstrap_samples > 0:
        counts, sums = grouped.rows.to_numpy(), grouped.brier_sum.to_numpy(float)
        scores = []
        for _ in range(bootstrap_samples):
            sample = rng.integers(0, len(grouped), len(grouped))
            scores.append(float(sums[sample].sum() / counts[sample].sum()))
        report["brier_storm_bootstrap_95_interval"] = np.quantile(scores, [.025, .975]).tolist()
    else:
        report["brier_storm_bootstrap_95_interval"] = None
    for col in ["operator", "previously_failed_in_training", "spatial_holdout", "storm_severity"]:
        if col in frame:
            report["by_" + col] = {str(key): metrics(d.target, d.probability, threshold)
                                    for key, d in frame.groupby(col, dropna=False)}
    ranked = frame.sort_values(["cutoff_utc", "probability", "asset_id"], ascending=[True, False, True])
    inspections = ranked.groupby("cutoff_utc", sort=False).head(10)
    report["recall_at_top_10_feeders_per_hour"] = float(inspections.target.sum() / frame.target.sum()) if frame.target.sum() else None
    if destination:
        write_json(destination, report)
    return report


def plot_calibration(report, destination):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    bins = report["reliability"]
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot([0, 1], [0, 1], "--", color="gray", label="Perfect calibration")
    ax.plot([b["mean_probability"] for b in bins], [b["observed_frequency"] for b in bins], "o-", label="Held-out exposure")
    ax.set(xlabel="Predicted hourly interruption probability", ylabel="Observed hourly frequency", title="Feeder probability calibration")
    ax.legend()
    fig.tight_layout()
    fig.savefig(destination, dpi=180)
    plt.close(fig)
