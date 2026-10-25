"""Imported probabilities replace fragility and respect source-scoped branch identity."""
from copy import deepcopy

import numpy as np
import pytest

from backend.app import app
from backend.probability import imported_scenarios, reduce_hazard, validate_profile
from backend.simulation import Network, simulate


@pytest.fixture()
def net():
    return Network()


@pytest.fixture()
def profile(net):
    return {"network": "ieee14", "prediction_unit": "branch", "source": "synthetic contract test; not a trained model",
            "mode": "synthetic_test", "branch_ids": [b["id"] for b in net.branches],
            "prediction": {"model_version": "synthetic-test", "training_cutoff": "2020-01-01T00:00:00Z", "issued_at": "2021-01-01T00:00:00Z"},
            "hours": [{"timestamp": f"2021-01-01T0{h}:00:00Z", "gust_kmh": [150.] * net.m,
                       "rain_mm_h": 20., "failure_probability": [0.] * net.m} for h in range(2)]}


def test_zero_imported_probability_overrides_extreme_weather(net, profile):
    p = validate_profile(profile, net)
    scenario = imported_scenarios(p, 26, 1)[0]
    scenario["draws"][:] = 0
    run = simulate(net, scenario)
    assert run["failed_lines"] == 0
    assert run["unserved_mwh"] == pytest.approx(0)


def test_supplied_probability_is_not_multiplied_by_fragility_or_exposure(net, profile):
    p = validate_profile(profile, net)
    scenario = imported_scenarios(p, 26, 1)[0]
    scenario["failure_probability"][0, 0] = .6
    scenario["draws"][:] = 1
    scenario["draws"][0, 0] = .59
    net.exposure[:] = 0
    run = simulate(net, scenario)
    assert run["direct_weather_failed_mask"][0] == 1


def test_reinforcement_changes_hazard_with_correct_endpoint_behavior():
    p = np.array([0, .01, .5, 1.])
    assert np.allclose(reduce_hazard(p), 1 - (1 - p) ** .25)
    assert np.array_equal(reduce_hazard(p, 0), np.zeros(4))
    assert np.allclose(reduce_hazard(p, 1), p)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(network="gb"), lambda p: p.update(prediction_unit="feeder"),
    lambda p: p["branch_ids"].reverse(), lambda p: p["hours"][0]["failure_probability"].pop(),
    lambda p: p["hours"][0]["failure_probability"].__setitem__(0, float("nan")),
    lambda p: p["hours"][0]["failure_probability"].__setitem__(0, 1.1),
    lambda p: p["hours"][0]["failure_probability"].__setitem__(0, True),
    lambda p: p["hours"][1].update(timestamp="2021-01-01T03:00:00Z"),
    lambda p: p["hours"][0].update(timestamp="2021-01-01T00:00:00"),
    lambda p: p["hours"][0].update(timestamp="2021-01-01T00:30:00Z"),
    lambda p: p["prediction"].update(training_cutoff="2022-01-01T00:00:00Z"),
    lambda p: p.update(hours=p["hours"] * 37),
])
def test_bad_profiles_are_rejected(net, profile, mutation):
    mutation(profile)
    with pytest.raises(ValueError):
        validate_profile(profile, net)


def test_forecast_issue_time_cannot_follow_prediction_cutoff(net, profile):
    profile["mode"] = "forecast"
    profile["prediction"]["issued_at"] = "2021-01-01T01:00:00Z"
    with pytest.raises(ValueError, match="issue time"):
        validate_profile(profile, net)


def test_api_import_preserves_metadata_and_separates_gb_network(profile):
    client = app.test_client()
    response = client.post("/api/storm-plan", json={"scenario": "imported", "weather": profile, "samples": 16, "budget": 0})
    assert response.status_code == 200
    data = response.get_json()
    assert data["portfolios"]["none"]["metrics"]["mean_failed_lines"] == 0
    assert data["weather"]["source"] == profile["source"]
    assert "Not implemented" in data["model"]["repairs"]
    gb = client.get("/api/network?name=gb")
    assert gb.status_code == 200
    assert gb.get_json()["network"] == "gb"
    assert len(gb.get_json()["branches"]) != len(profile["branch_ids"])
    assert client.get("/api/network?name=ieee14").get_json()["network"] == "ieee14"
    bad = client.post("/api/storm-plan", json={"scenario": "imported", "weather": profile, "crews": 6})
    assert bad.status_code == 400
    assert "crews" in bad.get_json()["error"]


def test_full_72_hour_profile_fits_request_limit_and_preserves_hourly_inputs(profile):
    from datetime import datetime, timedelta, timezone
    start = datetime(2021, 1, 1, tzinfo=timezone.utc)
    template = profile["hours"][0]
    profile["hours"] = [{**deepcopy(template), "timestamp": (start + timedelta(hours=h)).isoformat()} for h in range(72)]
    response = app.test_client().post("/api/storm-plan", json={"scenario": "imported", "weather": profile, "samples": 16, "budget": 0})
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["weather"]["hours"]) == 72
    assert data["weather"]["hours"][-1]["timestamp"] == (start + timedelta(hours=71)).isoformat()
    assert data["portfolios"]["none"]["metrics"]["mean_failed_lines"] == 0
