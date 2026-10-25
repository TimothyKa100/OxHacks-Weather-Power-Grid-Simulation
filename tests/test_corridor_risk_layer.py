"""Corridor outage risk is a display layer: served by its own endpoint, refused as simulator weather."""
import json

import pytest

from backend import app as app_module
from backend.weather import validate_weather


def profile(ids):
    hours = [{"timestamp": f"2024-12-06T{12 + h:02d}:00:00+00:00", "gust_kmh": 80., "rain_mm_h": 1.,
              "failure_probability": [0.01] * len(ids)} for h in range(2)]
    return {"source": "test", "prediction_unit": "line_corridor_area_risk", "name": "Test layer", "branch_ids": ids,
            "prediction": {"model_version": "v", "training_cutoff": "2023-01-01T00:00:00Z", "issued_at": "2024-12-06T12:00:00Z"},
            "hours": hours}


def test_corridor_risk_is_refused_as_line_failure_weather():
    with pytest.raises(ValueError, match="map layer"):
        validate_weather(profile(["L1"]), 1)


def test_endpoint_lists_and_serves_layers_in_network_order(tmp_path, monkeypatch):
    ids = [e["id"] for e in app_module.nets["gb"].branches]
    (tmp_path / "storm.json").write_text(json.dumps(profile(ids)))
    (tmp_path / "other.json").write_text(json.dumps({"source": "not a risk layer"}))
    monkeypatch.setattr(app_module, "RISK_DIR", tmp_path)
    client = app_module.app.test_client()
    layers = client.get("/api/corridor-risk").get_json()["layers"]
    assert [l["id"] for l in layers] == ["storm"]
    layer = client.get("/api/corridor-risk?id=storm").get_json()
    assert len(layer["timestamps"]) == 2 and len(layer["probability"][0]) == len(ids)
    assert client.get("/api/corridor-risk?id=missing").status_code == 404


def test_endpoint_rejects_a_layer_in_the_wrong_branch_order(tmp_path, monkeypatch):
    ids = [e["id"] for e in app_module.nets["gb"].branches][::-1]
    (tmp_path / "storm.json").write_text(json.dumps(profile(ids)))
    monkeypatch.setattr(app_module, "RISK_DIR", tmp_path)
    assert app_module.app.test_client().get("/api/corridor-risk?id=storm").status_code == 409
