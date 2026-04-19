from fastapi.testclient import TestClient

import app.main as main_module
import app.predictor as predictor_module


def test_startup_loads_model(monkeypatch):
    called = {"value": 0}

    def fake_load_model():
        called["value"] += 1

    monkeypatch.setattr(main_module, "load_model", fake_load_model)

    with TestClient(main_module.app):
        pass

    assert called["value"] == 1


def test_root_returns_welcome_message(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Welcome to the Ecommerce Ticket Assistant API",
    }


def test_health_reports_model_not_loaded(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model_loaded": False,
    }


def test_health_reports_model_loaded(client, mocked_model):
    predictor_module.MODEL = mocked_model

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "model_loaded": True,
    }


def test_predict_success(client, mocked_model, sample_payload):
    predictor_module.MODEL = mocked_model

    response = client.post("/predict", json=sample_payload)

    assert response.status_code == 200
    assert response.json() == {
        "priority": "high_priority",
        "category": "ticket_classification",
        "confidence": 0.90,
        "model_version": "v3",
    }
    mocked_model.predict.assert_called_once_with([sample_payload["text"]])


def test_predict_validation_error_for_short_text(client):
    response = client.post("/predict", json={"text": "bad"})

    assert response.status_code == 422
    body = response.json()
    assert body["detail"]
    assert any(item["loc"][-1] == "text" for item in body["detail"])


def test_predict_validation_error_for_missing_text(client):
    response = client.post("/predict", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["detail"]
    assert any(item["loc"][-1] == "text" for item in body["detail"])


def test_predict_internal_failure_returns_500(monkeypatch, sample_payload):
    def failing_predict_ticket(_text):
        raise RuntimeError("prediction failed")

    monkeypatch.setattr(main_module, "load_model", lambda: None)
    monkeypatch.setattr(main_module, "predict_ticket", failing_predict_ticket)

    with TestClient(main_module.app, raise_server_exceptions=False) as client:
        response = client.post("/predict", json=sample_payload)

    assert response.status_code == 500
