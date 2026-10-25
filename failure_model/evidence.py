"""Separate incident evidence from verified labels and known operating exposure."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .common import ROOT, now, require_columns, sha256, write_json

EVENT_SCHEMA = pa.schema([
    ("event_id", pa.string()), ("source_event_key", pa.string()), ("asset_id", pa.string()),
    ("onset_utc", pa.timestamp("ns", tz="UTC")), ("restored_at_utc", pa.timestamp("ns", tz="UTC")),
    ("planned_status", pa.string()), ("cause_class", pa.string()),
    ("onset_verified", pa.bool_()), ("classification_verified", pa.bool_()),
    ("identity_verified", pa.bool_()), ("verification_ref", pa.string()),
])
AVAILABILITY_SCHEMA = pa.schema([
    ("asset_id", pa.string()), ("start_utc", pa.timestamp("ns", tz="UTC")),
    ("end_utc", pa.timestamp("ns", tz="UTC")), ("in_service", pa.bool_()),
    ("reporting_complete", pa.bool_()), ("state_history_complete", pa.bool_()),
    ("operational_at_start", pa.bool_()), ("identity_verified", pa.bool_()),
    ("verification_ref", pa.string()),
])


def initialize_evidence(output):
    evidence = Path(output) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    for name, schema in [("verified_events", EVENT_SCHEMA), ("availability", AVAILABILITY_SCHEMA)]:
        path = evidence / f"{name}.parquet"
        if not path.exists():
            pq.write_table(pa.Table.from_batches([], schema=schema), path)
    write_json(evidence / "contract.json", {
        "events": {f.name: str(f.type) for f in EVENT_SCHEMA},
        "availability": {f.name: str(f.type) for f in AVAILABILITY_SCHEMA},
        "planned_status": ["unplanned", "planned", "unknown"],
        "cause_class": ["wind_weather", "other", "unknown"],
        "rules": ["Populate only from verified source evidence; retain a document/record reference",
                  "source_event_key resolves the matching quarantined incident; do not invent matches",
                  "Availability describes reporting/in-service coverage and known initial operating state",
                  "state_history_complete certifies all interruptions and state transitions within the interval",
                  "Unknown restoration censors subsequent hours; source averages are not restoration",
                  "All verified outages, including planned/non-weather events, must be supplied to censor downtime",
                  "Current snapshots alone cannot certify historical asset identity"]})


def associate_incidents(output, processed=None):
    output = Path(output)
    processed = Path(processed or ROOT / "data/processed")
    assets = pd.read_parquet(output / "assets.parquet")
    source = pd.read_parquet(processed / "fault_candidates_UNVERIFIED.parquet")
    if not source.event_key.is_unique:
        raise ValueError("Duplicate source event keys require investigation")
    associations = source.merge(assets[["asset_id", "gis_circuit_key", "operator", "voltage_kv"]].rename(columns={"voltage_kv": "asset_voltage_kv"}),
                                left_on=["feeder_key_candidate", "operator"],
                                right_on=["asset_id", "operator"], validate="many_to_one")
    associations["reported_hour_utc_provisional"] = associations.reported_at_utc_assuming_london.dt.floor("h")
    associations["onset_verified"] = False
    associations["label_status"] = "quarantined_not_supervised_labels"
    associations["voltage_match_status"] = np.where(
        associations.voltage_1.isna() | associations.asset_voltage_kv.isna(), "unknown",
        np.where(np.isclose(associations.voltage_1, associations.asset_voltage_kv), "consistent", "requires_source_review"))
    associations.to_parquet(output / "incident_associations.parquet", index=False)
    initialize_evidence(output)
    review = associations[["event_key", "asset_id", "gis_circuit_key", "operator", "district_short_code", "reporting_year",
                           "dist_hv_ref", "reported_at_local", "reported_at_utc_assuming_london", "source_resource",
                           "voltage_1", "asset_voltage_kv", "voltage_match_status"]].copy()
    review["requested_evidence"] = "planned_status; onset_and_timezone; restoration; historical_identity; reporting_and_state_coverage"
    review.to_parquet(output / "evidence/source_review_queue.parquet", index=False)
    report = {"generated_utc": now(), "source_candidates": len(source), "cohort_candidates": len(associations),
              "cohort_feeders_with_candidates": int(associations.asset_id.nunique()),
              "unresolved_utc_times": int(associations.reported_hour_utc_provisional.isna().sum()),
              "verified_label_count": 0, "training_ready": False,
              "planned_status_counts": associations.planned_status.value_counts().to_dict(),
              "voltage_match_status_counts": associations.voltage_match_status.value_counts().to_dict(),
              "cause_counts_for_research_only": associations.cause.value_counts().to_dict(),
              "source_sha256": sha256(processed / "fault_candidates_UNVERIFIED.parquet")}
    write_json(output / "incident_report.json", report)
    return report


def _bool_fields(frame, columns, name):
    for c in columns:
        if frame[c].isna().any() or not frame[c].map(lambda v: isinstance(v, (bool, np.bool_))).all():
            raise ValueError(f"{name}.{c} must contain explicit booleans")


def validate_evidence(events, availability):
    require_columns(events, EVENT_SCHEMA.names, "events")
    require_columns(availability, AVAILABILITY_SCHEMA.names, "availability")
    for name, frame, fields in [("events", events, ["onset_utc", "restored_at_utc"]),
                                 ("availability", availability, ["start_utc", "end_utc"])]:
        for c in fields:
            if not isinstance(frame[c].dtype, pd.DatetimeTZDtype) or str(frame[c].dt.tz) != "UTC":
                raise ValueError(f"{name}.{c} must be UTC timestamps, including empty schemas")
        for c in ["asset_id", "verification_ref"]:
            if frame[c].isna().any() or frame[c].str.strip().eq("").any():
                raise ValueError(f"{name}.{c} needs an actual identifier/evidence reference")
    _bool_fields(events, ["onset_verified", "classification_verified", "identity_verified"], "events")
    _bool_fields(availability, ["in_service", "reporting_complete", "state_history_complete",
                                "operational_at_start", "identity_verified"], "availability")
    if events.event_id.isna().any() or not events.event_id.is_unique or events.source_event_key.dropna().duplicated().any():
        raise ValueError("Event IDs and non-null source event keys must be unique")
    if events.onset_utc.isna().any() or (events.restored_at_utc.notna() & events.restored_at_utc.le(events.onset_utc)).any():
        raise ValueError("Events require onset and restoration later than onset when known")
    if not events.planned_status.isin(["unplanned", "planned", "unknown"]).all():
        raise ValueError("Unrecognized planned status")
    if not events.cause_class.isin(["wind_weather", "other", "unknown"]).all():
        raise ValueError("Unrecognized cause class")
    if availability[["start_utc", "end_utc"]].isna().any().any() or availability.end_utc.le(availability.start_utc).any():
        raise ValueError("Availability needs increasing finite intervals")
    for _, intervals in availability.groupby("asset_id"):
        intervals = intervals.sort_values("start_utc")
        if intervals.start_utc.iloc[1:].reset_index(drop=True).lt(intervals.end_utc.iloc[:-1].reset_index(drop=True)).any():
            raise ValueError("Overlapping availability intervals are ambiguous")


def label_rows(keys, events, availability, candidates, target="any_unplanned"):
    """A verified interval and complete event/state evidence are required for every zero."""
    if target not in ("any_unplanned", "wind_weather"):
        raise ValueError("Unknown target")
    validate_evidence(events, availability)
    out = keys[["asset_id", "cutoff_utc"]].copy().reset_index(drop=True)
    out["target"] = pd.array([pd.NA] * len(out), dtype="Int8")
    out["at_risk_verified"] = False
    out["label_verified"] = False
    out["event_id"] = pd.Series(None, index=out.index, dtype="string")
    out["censor_reason"] = "no_verified_reporting_and_state_interval"
    event_map = {asset: group.sort_values("onset_utc") for asset, group in events.groupby("asset_id")}
    resolved = events.loc[events.onset_verified & events.classification_verified & events.identity_verified &
                          events.planned_status.ne("unknown"), "source_event_key"]
    unresolved = candidates[~candidates.event_key.isin(resolved.dropna())]
    if not candidates.empty and not events.empty:
        evidence = events.dropna(subset=["source_event_key"]).merge(
            candidates[["event_key", "asset_id"]], left_on="source_event_key", right_on="event_key", suffixes=("", "_source"))
        if evidence.asset_id.ne(evidence.asset_id_source).any():
            raise ValueError("Verified event contradicts the incident asset association")
    for interval in availability.itertuples():
        mask = (out.asset_id.eq(interval.asset_id) & out.cutoff_utc.ge(interval.start_utc) &
                (out.cutoff_utc + pd.Timedelta(hours=1)).le(interval.end_utc))
        if not mask.any():
            continue
        if not all([interval.in_service, interval.reporting_complete, interval.state_history_complete,
                    interval.operational_at_start, interval.identity_verified]):
            out.loc[mask, "censor_reason"] = "interval_not_verified_in_service_operating_reporting_complete"
            continue
        unknown = unresolved[unresolved.asset_id.eq(interval.asset_id)]
        # Unknown reporting delay makes a partial hour-only exclusion unsafe.
        if unknown.reported_hour_utc_provisional.isna().any() or unknown.reported_hour_utc_provisional.between(
                interval.start_utc, interval.end_utc, inclusive="left").any():
            out.loc[mask, "censor_reason"] = "unresolved_incident_in_coverage_interval"
            continue
        out.loc[mask, "target"] = 0
        out.loc[mask, ["at_risk_verified", "label_verified"]] = True
        out.loc[mask, "censor_reason"] = ""
        for event in event_map.get(interval.asset_id, events.iloc[:0]).itertuples():
            if event.onset_utc >= interval.end_utc:
                continue
            restoration = event.restored_at_utc if pd.notna(event.restored_at_utc) else interval.end_utc
            # The onset hour starts operating; subsequent hours starting during downtime are censored.
            down = mask & out.cutoff_utc.ge(event.onset_utc) & out.cutoff_utc.lt(restoration)
            onset = mask & out.cutoff_utc.le(event.onset_utc) & (out.cutoff_utc + pd.Timedelta(hours=1)).gt(event.onset_utc)
            verified = event.onset_verified and event.classification_verified and event.identity_verified
            qualifies = verified and event.planned_status == "unplanned" and (
                target == "any_unplanned" or event.cause_class == "wind_weather")
            uncertain_cause = target == "wind_weather" and event.cause_class == "unknown"
            eligible = onset & out.at_risk_verified & qualifies & (not uncertain_cause)
            censor = down | (onset & ~eligible)
            out.loc[censor, "target"] = pd.NA
            out.loc[censor, ["at_risk_verified", "label_verified"]] = False
            out.loc[censor, "censor_reason"] = "outage_planned_competing_or_unverified_event"
            # Exact-hour onset is a new failure, not an asset already down before the window.
            out.loc[eligible, "target"] = 1
            out.loc[eligible, ["at_risk_verified", "label_verified"]] = True
            out.loc[eligible, "event_id"] = event.event_id
            out.loc[eligible, "censor_reason"] = ""
    return out


def build_labels(output, target="any_unplanned"):
    output = Path(output)
    initialize_evidence(output)
    events = pd.read_parquet(output / "evidence/verified_events.parquet")
    availability = pd.read_parquet(output / "evidence/availability.parquet")
    candidates = pd.read_parquet(output / "incident_associations.parquet")
    validate_evidence(events, availability)
    reasons, rows, positives, negatives = {}, 0, 0, 0
    for path in sorted((output / "features").glob("*.parquet")):
        if events.empty and availability.empty:
            size = pq.ParquetFile(path).metadata.num_rows
            label_path = output / "labels" / path.name
            old = pq.ParquetFile(label_path) if label_path.exists() else None
            safely_quarantined = old is not None and old.metadata.num_rows == size
            if old is not None:
                target_column = old.schema_arrow.names.index("target")
                flag_column = old.schema_arrow.names.index("label_verified")
                for i in range(old.metadata.num_row_groups):
                    group = old.metadata.row_group(i)
                    target_stats = group.column(target_column).statistics
                    flag_stats = group.column(flag_column).statistics
                    safely_quarantined &= bool(target_stats is not None and target_stats.null_count == group.num_rows and
                                               flag_stats is not None and flag_stats.max is False)
            if not safely_quarantined:
                # Withdrawing evidence must also withdraw previously materialized labels.
                keys = pd.read_parquet(path, columns=["asset_id", "cutoff_utc"])
                labels = keys.copy()
                labels["target"] = pd.array([pd.NA] * size, dtype="Int8")
                labels["at_risk_verified"] = False
                labels["label_verified"] = False
                labels["censor_reason"] = "unverified_events_availability_and_identity"
                labels.to_parquet(label_path, index=False, compression="zstd")
            rows += size
            reasons["unverified_events_availability_and_identity"] = reasons.get("unverified_events_availability_and_identity", 0) + size
            continue
        features = pd.read_parquet(path, columns=["asset_id", "cutoff_utc", "gust_max_prev_72h_kmh", "precipitation_prev_72h_mm"])
        keys = features[["asset_id", "cutoff_utc"]]
        labels = label_rows(keys, events, availability, candidates, target)
        incomplete = features[["gust_max_prev_72h_kmh", "precipitation_prev_72h_mm"]].isna().any(axis=1)
        labels.loc[incomplete, "target"] = pd.NA
        labels.loc[incomplete, ["at_risk_verified", "label_verified"]] = False
        labels.loc[incomplete, "censor_reason"] = "incomplete_route_weather_or_72h_history"
        dest = output / "labels" / path.name
        labels.to_parquet(dest.with_suffix(".tmp"), index=False, compression="zstd")
        dest.with_suffix(".tmp").replace(dest)
        rows += len(labels)
        positives += int(labels.target.eq(1).sum())
        negatives += int(labels.target.eq(0).sum())
        for key, count in labels.censor_reason[labels.target.isna()].value_counts().items():
            reasons[key] = reasons.get(key, 0) + int(count)
    result = {"generated_utc": now(), "target": target, "rows": rows, "verified_events": len(events),
              "availability_intervals": len(availability), "positive_rows": positives, "negative_rows": negatives,
              "censored_rows": rows - positives - negatives, "censor_reasons": reasons,
              "training_ready": positives > 0 and negatives > 0,
              "evidence_sha256": {name: sha256(output / "evidence" / name) for name in ["verified_events.parquet", "availability.parquet"]}}
    write_json(output / "label_report.json", result)
    return result


def link_incident_features(output):
    """Research associations at provisional reported hours, never supervised targets."""
    output = Path(output)
    incidents = pd.read_parquet(output / "incident_associations.parquet")
    rows = []
    month = incidents.reported_hour_utc_provisional.dt.strftime("%Y-%m")
    for key, group in incidents.groupby(month):
        path = output / "features" / f"{key}.parquet"
        if not path.exists():
            continue
        features = pd.read_parquet(path)
        linked = group[["event_key", "asset_id", "reported_hour_utc_provisional", "cause", "contributory_cause", "planned_status"]].merge(
            features, left_on=["asset_id", "reported_hour_utc_provisional"], right_on=["asset_id", "cutoff_utc"],
            how="left", validate="many_to_one")
        linked["association_status"] = "provisional_report_time_not_verified_onset"
        linked["training_eligible"] = False
        rows.append(linked)
    linked = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    linked.to_parquet(output / "incident_weather_research.parquet", index=False, compression="zstd")
    return {"linked_research_rows": len(linked), "unresolved_time_rows_excluded": int(incidents.reported_hour_utc_provisional.isna().sum()),
            "not_supervised_labels": True}
