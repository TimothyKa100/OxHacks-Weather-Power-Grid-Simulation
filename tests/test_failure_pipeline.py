"""Semantic tests for leakage, exposure, route weighting and honest model gates."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from failure_model.common import FEATURES, STATIC_FEATURES
from failure_model.evidence import EVENT_SCHEMA, AVAILABILITY_SCHEMA, label_rows, validate_evidence, build_labels, initialize_evidence
from failure_model.features import derive_features, weighted_percentile, crosswind_envelope, FeatureBuilder
from failure_model.inventory import route_intervals, cell_key
from failure_model.splits import assign_splits
from failure_model.training import TrainingNotReady, train, train_frames
from failure_model.evaluation import evaluate


def empty(schema):
    return pa.Table.from_batches([], schema=schema).to_pandas()


def raw_weather(n=120):
    return pd.DataFrame({"gust_max": 90., "gust_p95": 80., "crosswind_max": 60., "wind_mean": 40.,
                         "precipitation": np.arange(n, dtype=float), "temperature": 10., "soil_moisture": .3},
                        index=pd.date_range("2021-01-01", periods=n, freq="h", tz="UTC"))


def test_instantaneous_features_exclude_current_and_future_samples():
    raw = raw_weather()
    before = derive_features(raw)
    raw.loc[raw.index[80]:, ["gust_max", "gust_p95", "crosswind_max", "wind_mean", "temperature", "soil_moisture"]] = 1000
    after = derive_features(raw)
    pd.testing.assert_frame_equal(before.iloc[:81], after.iloc[:81])
    assert after.gust_route_max_lag_1h_kmh.iloc[81] == 1000


def test_precipitation_uses_completed_hour_not_future_interval():
    raw = raw_weather()
    result = derive_features(raw)
    assert result.precipitation_prev_1h_mm.iloc[24] == 24
    assert result.precipitation_prev_24h_mm.iloc[24] == sum(range(1, 25))
    assert pd.isna(result.gust_max_prev_72h_kmh.iloc[71])
    assert result.gust_max_prev_72h_kmh.iloc[72] == 90
    raw.iloc[25:, :] = 1000
    pd.testing.assert_frame_equal(result.iloc[:25], derive_features(raw).iloc[:25])


def test_missing_sample_cannot_become_zero_exposure():
    raw = raw_weather()
    raw.loc[raw.index[30], "gust_max"] = np.nan
    result = derive_features(raw)
    assert pd.isna(result.gust_ge_80_sample_hours_prev_24h.iloc[31])
    assert pd.isna(result.gust_max_prev_24h_kmh.iloc[31])
    assert result.gust_ge_80_sample_hours_prev_24h.iloc[55] == 24


def test_all_threshold_windows_and_accumulated_intensity_are_present():
    raw = raw_weather()
    raw["temperature"] = -10.
    raw["gust_max"] = 125.
    raw["precipitation"] = 12.
    result = derive_features(raw)
    for hours in (6, 24, 72):
        assert result[f"cold_le_minus5_sample_hours_prev_{hours}h"].iloc[80] == hours
        assert result[f"heat_ge_30_sample_hours_prev_{hours}h"].iloc[80] == 0
        assert result[f"precipitation_ge_10_sample_hours_prev_{hours}h"].iloc[80] == hours
        assert result[f"gust_ge_120_sample_hours_prev_{hours}h"].iloc[80] == hours
    assert result.cold_excess_minus5_prev_24h_c_sample_hours.iloc[80] == 120
    assert result.gust_excess_120_prev_24h_kmh_sample_hours.iloc[80] == 120
    assert result.precipitation_excess_10_prev_24h_mm.iloc[80] == 48


def test_route_quantile_is_length_weighted_and_missing_is_preserved():
    values = np.array([[20., 100.], [20., 100.], [20., np.nan]])
    assert weighted_percentile(values, [.99, .01])[0] == 20
    assert weighted_percentile(values, [.9, .1])[0] == 100
    assert np.isnan(weighted_percentile(values, [.9, .1])[2])
    envelope = crosswind_envelope([0, 45])
    assert envelope[90] == pytest.approx(1)
    assert envelope[0] == pytest.approx(np.sin(np.pi / 4))
    assert cell_key(51.5, -.001) == "51.50_0.00"


def test_route_intervals_do_not_connect_different_parts_or_assets():
    p = pd.DataFrame({"node_id": ["a", "a", "a", "a", "b", "b"], "part": [0, 0, 1, 1, 0, 0],
                      "distance_m": [0., 100., 0., 50., 0., 200.], "easting": [400000., 400100., 400200., 400250., 400300., 400500.],
                      "northing": 200000., "terrain_elevation_m": [10., 20., 100., 110., 5., np.nan]})
    intervals = route_intervals(p)
    assert intervals.route_length_m.sum() == 350
    assert len(intervals) == 3
    assert intervals.terrain_valid_length_m.sum() == 150
    assert intervals.terrain_integral_m2.sum() == 15 * 100 + 105 * 50


def test_vectorized_features_match_single_asset_computation(tmp_path):
    builder = FeatureBuilder.__new__(FeatureBuilder)
    builder.assets = pd.DataFrame([{"asset_id": "a", "route_weather_complete": True, **{c: float(i + 1) for i, c in enumerate(STATIC_FEATURES)}}])
    builder.links = pd.DataFrame({"asset_id": ["a", "a"], "weather_key": ["x", "y"],
                                  "length_fraction": [.7, .3], "bearing_degrees": [[0., 30.], [80.]]})
    builder.groups = {"a": builder.links}
    builder.envelopes = {("a", r.weather_key): crosswind_envelope(r.bearing_degrees) for r in builder.links.itertuples()}
    index = pd.date_range("2021-01-01", periods=120, freq="h", tz="UTC")
    weather = {}
    for key, gust in [("x", 90.), ("y", 110.)]:
        weather[key] = pd.DataFrame({"wind_gusts_10m": gust, "wind_speed_10m": 40., "wind_direction_10m": np.arange(120) % 360,
                                     "temperature_2m": 10., "precipitation": 1., "soil_moisture_0_to_7cm": .3}, index=index)
    asset = next(builder.assets.itertuples())
    scalar = builder.asset_features(asset, index, weather).loc[index[72]:, FEATURES]
    batched = builder.batch_features(index, weather, index[72]).to_pandas()
    assert np.allclose(scalar, batched[FEATURES], equal_nan=True, rtol=2e-6, atol=2e-5)


def evidence():
    events = pa.Table.from_pylist([{"event_id": "event", "source_event_key": "source", "asset_id": "a",
                                    "onset_utc": pd.Timestamp("2021-01-01T02:24Z"), "restored_at_utc": pd.Timestamp("2021-01-01T04:30Z"),
                                    "planned_status": "unplanned", "cause_class": "wind_weather", "onset_verified": True,
                                    "classification_verified": True, "identity_verified": True, "verification_ref": "synthetic-test-only"}], schema=EVENT_SCHEMA).to_pandas()
    availability = pa.Table.from_pylist([{"asset_id": "a", "start_utc": pd.Timestamp("2021-01-01T00:00Z"),
                                         "end_utc": pd.Timestamp("2021-01-01T08:00Z"), "in_service": True,
                                         "reporting_complete": True, "state_history_complete": True,
                                         "operational_at_start": True, "identity_verified": True, "verification_ref": "synthetic-test-only"}], schema=AVAILABILITY_SCHEMA).to_pandas()
    keys = pd.DataFrame({"asset_id": "a", "cutoff_utc": pd.date_range("2021-01-01", periods=8, freq="h", tz="UTC")})
    candidates = pd.DataFrame({"event_key": ["source"], "asset_id": ["a"], "reported_hour_utc_provisional": [pd.Timestamp("2021-01-01T02:00Z")]})
    return keys, events, availability, candidates


def test_verified_onset_and_restoration_censor_actual_downtime():
    result = label_rows(*evidence())
    assert result.target.iloc[:3].tolist() == [0, 0, 1]
    assert result.target.iloc[3:5].isna().all()
    assert result.target.iloc[5:].tolist() == [0, 0, 0]
    assert not result.at_risk_verified.iloc[3:5].any()


def test_unreported_is_not_a_negative_without_verified_exposure():
    keys, events, _, candidates = evidence()
    result = label_rows(keys, events, empty(AVAILABILITY_SCHEMA), candidates)
    assert result.target.isna().all()
    assert not result.at_risk_verified.any()


def test_unknown_restoration_censors_every_following_hour():
    keys, events, availability, candidates = evidence()
    events["restored_at_utc"] = pd.NaT
    events["restored_at_utc"] = pd.to_datetime(events.restored_at_utc, utc=True)
    result = label_rows(keys, events, availability, candidates)
    assert result.target.iloc[2] == 1
    assert result.target.iloc[3:].isna().all()


def test_exact_hour_onset_is_a_new_failure_in_that_hour():
    keys, events, availability, candidates = evidence()
    events["onset_utc"] = pd.Timestamp("2021-01-01T02:00Z")
    result = label_rows(keys, events, availability, candidates)
    assert result.target.iloc[2] == 1
    assert result.target.iloc[3:5].isna().all()


def test_unresolved_source_candidate_blocks_false_healthy_labels():
    keys, events, availability, candidates = evidence()
    events["planned_status"] = "unknown"
    result = label_rows(keys, events, availability, candidates)
    assert result.target.isna().all()
    assert result.censor_reason.eq("unresolved_incident_in_coverage_interval").all()


def test_planned_and_nonweather_outages_censor_instead_of_becoming_wind_failures():
    keys, events, availability, candidates = evidence()
    events["planned_status"] = "planned"
    result = label_rows(keys, events, availability, candidates, "wind_weather")
    assert result.target.iloc[2:5].isna().all()
    assert result.target.iloc[0] == 0


def test_overlapping_coverage_and_naive_timestamps_are_rejected():
    _, events, availability, _ = evidence()
    with pytest.raises(ValueError, match="Overlapping"):
        validate_evidence(events, pd.concat([availability, availability]))
    events["onset_utc"] = events.onset_utc.dt.tz_localize(None)
    with pytest.raises(ValueError, match="UTC"):
        validate_evidence(events, availability)


def test_storm_crossing_calendar_boundary_is_purged_whole():
    frame = pd.DataFrame({"asset_id": "a", "cutoff_utc": pd.to_datetime(["2021-12-20T00:00Z", "2022-01-05T00:00Z", "2022-06-01T00:00Z"])})
    windows = pd.DataFrame({"storm_id": ["crossing"], "start_utc": [pd.Timestamp("2021-12-19T00:00Z")],
                            "end_utc": [pd.Timestamp("2022-01-06T00:00Z")]})
    result = assign_splits(frame, windows, {"a": "primary"})
    assert result.split.tolist() == ["purged", "purged", "tune"]


def test_training_stops_before_creating_model_without_verified_labels(tmp_path):
    model = tmp_path / "model"
    with pytest.raises(TrainingNotReady, match="No verified positive"):
        train(tmp_path, model)
    assert not model.exists()
    report = json.loads((tmp_path / "training_readiness.json").read_text())
    assert report["model_trained"] is False


def test_withdrawing_evidence_removes_previously_materialized_labels(tmp_path):
    initialize_evidence(tmp_path)
    (tmp_path / "features").mkdir()
    (tmp_path / "labels").mkdir()
    keys = pd.DataFrame({"asset_id": ["a"], "cutoff_utc": pd.to_datetime(["2021-01-01T00:00Z"])})
    keys.to_parquet(tmp_path / "features/2021-01.parquet", index=False)
    old = keys.assign(target=1, at_risk_verified=True, label_verified=True, censor_reason="")
    old.to_parquet(tmp_path / "labels/2021-01.parquet", index=False)
    pd.DataFrame({"event_key": [], "asset_id": [], "reported_hour_utc_provisional": []}).to_parquet(tmp_path / "incident_associations.parquet", index=False)
    report = build_labels(tmp_path)
    assert report["positive_rows"] == 0
    labels = pd.read_parquet(tmp_path / "labels/2021-01.parquet")
    assert labels.target.isna().all()
    assert not labels.label_verified.any()


def test_evaluation_rejects_unverified_and_sampled_test_exposure():
    f = pd.DataFrame({"asset_id": ["a", "a"], "cutoff_utc": pd.date_range("2021-01-01", periods=2, freq="h", tz="UTC"),
                      "storm_id": ["x", "y"], "target": [0, 1], "probability": [.1, .9],
                      "at_risk_verified": True, "label_verified": True})
    assert evaluate(f, bootstrap_samples=10)["brier_score"] == pytest.approx(.01)
    f["sampling_weight"] = 2
    with pytest.raises(ValueError, match="full exposure"):
        evaluate(f)
    f["sampling_weight"] = 1
    f.loc[0, "label_verified"] = False
    with pytest.raises(ValueError, match="Unverified"):
        evaluate(f)


def test_xgboost_calibration_and_artifacts_on_synthetic_fixture_only(tmp_path):
    pytest.importorskip("xgboost")
    rng = np.random.default_rng(26)
    frames = []
    for split, year in [("train", 2020), ("tune", 2022), ("calibrate", 2023), ("test", 2024)]:
        n = 240
        f = pd.DataFrame(rng.normal(size=(n, len(FEATURES))), columns=FEATURES)
        f["asset_id"] = np.where(np.arange(n) % 2, "synthetic-a", "synthetic-b")
        f["cutoff_utc"] = pd.date_range(f"{year}-05-01", periods=n, freq="h", tz="UTC")
        f["target"] = rng.random(n) < (.15 + .25 * (f.gust_route_max_lag_1h_kmh > 0))
        f["split"] = split
        f["storm_id"] = [f"{split}-{i // 48}" for i in range(n)]
        f["spatial_holdout"] = False
        f["at_risk_verified"] = True
        f["label_verified"] = True
        f["sampling_weight"] = 1.
        frames.append(f)
    result = train_frames(pd.concat(frames, ignore_index=True), tmp_path / "synthetic-only", target="wind_weather")
    assert result["prediction_unit"] == "feeder"
    assert result["prediction_target"] == "wind_weather"
    assert (tmp_path / "synthetic-only/temporal_calibration.png").exists()
    predictions = pd.read_parquet(tmp_path / "synthetic-only/held_out_predictions.parquet")
    assert predictions.probability.between(0, 1).all()
    assert predictions.cutoff_utc.dt.year.eq(2024).all()
    assert result["calibration"]["events"] == int(frames[2].target.sum())
    from failure_model.predict import predict
    inference = predict(frames[3], tmp_path / "synthetic-only", "2026-10-24T00:00:00Z")
    assert inference.failure_probability.between(0, 1).all()
    assert inference.prediction_unit.eq("feeder").all()
    assert inference.prediction_target.eq("wind_weather").all()
    with pytest.raises(ValueError, match="precede"):
        predict(frames[0], tmp_path / "synthetic-only", "2026-10-24T00:00:00Z")
