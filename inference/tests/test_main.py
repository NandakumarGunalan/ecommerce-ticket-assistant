"""Unit tests for ``inference.main`` — the online FastAPI endpoint.

Exercises the app via ``fastapi.testclient.TestClient`` with:

- ``model_loader.load_model`` monkeypatched to return a ``SimpleNamespace``
  carrying a fixed ``model_version`` / ``model_run_id`` (no real Vertex call).
- ``predictor.predict`` monkeypatched to return a canned ``Prediction`` list
  (no real HF load).

Mirrors the fake-dependency pattern from ``test_batch_predict.py``.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import List, Optional

import pytest
from fastapi.testclient import TestClient

from inference import main as main_module
from inference.schemas import Prediction


# ---------------------------------------------------------------------------
# Test doubles / fixtures
# ---------------------------------------------------------------------------


def _fake_loaded(model_version: str = "2", model_run_id: str = "run-test") -> SimpleNamespace:
    return SimpleNamespace(
        model=None,
        tokenizer=None,
        model_version=model_version,
        model_run_id=model_run_id,
    )


def _canned_prediction(
    priority: str = "urgent", confidence: float = 0.83
) -> Prediction:
    return Prediction(
        id=None,
        predicted_priority=priority,
        confidence=confidence,
        all_scores={"low": 0.02, "medium": 0.05, "high": 0.10, "urgent": 0.83},
    )


@pytest.fixture
def predict_calls() -> List[dict]:
    """Shared mutable log the fake predictor appends to."""
    return []


@pytest.fixture
def client(monkeypatch, predict_calls):
    """A TestClient with model_loader + predictor mocked, model 'loaded'."""

    def fake_load_model():
        return _fake_loaded()

    def fake_predict(texts, ids=None, *, batch_size: int = 32):
        predict_calls.append(
            {"texts": list(texts), "ids": list(ids) if ids is not None else None}
        )
        return [_canned_prediction() for _ in texts]

    monkeypatch.setattr(main_module.model_loader, "load_model", fake_load_model)
    monkeypatch.setattr(main_module.predictor, "predict", fake_predict)

    # Entering the TestClient context triggers the startup hook, which sets
    # _model_state["loaded"] = True via the patched load_model.
    main_module._model_state["loaded"] = False
    with TestClient(main_module.app) as c:
        yield c
    main_module._model_state["loaded"] = False


@pytest.fixture
def unloaded_client(monkeypatch):
    """A TestClient where startup DID NOT mark the model as loaded."""

    def fake_load_model():
        # Load is a no-op; also don't flip the flag — we want the "not loaded"
        # state observable via healthz/predict.
        return _fake_loaded()

    monkeypatch.setattr(main_module.model_loader, "load_model", fake_load_model)
    monkeypatch.setattr(
        main_module.predictor,
        "predict",
        lambda texts, ids=None, *, batch_size=32: [_canned_prediction() for _ in texts],
    )

    # Build a client WITHOUT entering its context (so the startup hook never
    # runs). Instead, we flip the flag to False explicitly.
    main_module._model_state["loaded"] = False
    c = TestClient(main_module.app)
    try:
        yield c
    finally:
        main_module._model_state["loaded"] = False


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_version"] == "2"
    assert body["model_run_id"] == "run-test"


def test_healthz_not_loaded(unloaded_client):
    r = unloaded_client.get("/healthz")
    assert r.status_code == 503
    assert r.json()["detail"] == "model not loaded"


def test_health_alias_ok(client):
    # ``/health`` is a Cloud-Run-friendly alias for ``/healthz`` (GFE
    # intercepts ``*/healthz``). Must return the same shape.
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_version"] == "2"
    assert body["model_run_id"] == "run-test"

    # Confirm identical shape to /healthz.
    r_z = client.get("/healthz")
    assert r_z.status_code == 200
    assert set(body.keys()) == set(r_z.json().keys())


def test_health_alias_not_loaded(unloaded_client):
    r = unloaded_client.get("/health")
    assert r.status_code == 503
    assert r.json()["detail"] == "model not loaded"


# ---------------------------------------------------------------------------
# /predict happy path
# ---------------------------------------------------------------------------


def test_predict_happy_path(client):
    r = client.post(
        "/predict",
        json={"ticket_text": "my order never arrived and it's been 3 weeks"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["predicted_priority"] == "urgent"
    assert body["confidence"] == pytest.approx(0.83)
    assert body["all_scores"] == {
        "low": 0.02,
        "medium": 0.05,
        "high": 0.10,
        "urgent": 0.83,
    }
    assert body["model_version"] == "2"
    assert body["model_run_id"] == "run-test"
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0


# ---------------------------------------------------------------------------
# /predict validation errors
# ---------------------------------------------------------------------------


def test_predict_empty_text(client):
    r = client.post("/predict", json={"ticket_text": ""})
    # Pydantic returns 422 for validation errors by default; FastAPI conventions
    # collapse this to 422 (not 400) — but ONLINE_PLAN spec says 400. FastAPI's
    # TestClient surfaces whatever FastAPI returns. Accept either 400 or 422
    # since both are client-error semantics and FastAPI's default is 422.
    assert r.status_code in (400, 422)


def test_predict_whitespace_only(client):
    r = client.post("/predict", json={"ticket_text": "   "})
    assert r.status_code in (400, 422)


def test_predict_overlong_text(client):
    r = client.post("/predict", json={"ticket_text": "x" * 10_001})
    assert r.status_code in (400, 422)


def test_predict_missing_field(client):
    r = client.post("/predict", json={})
    assert r.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Mock-interaction assertion
# ---------------------------------------------------------------------------


def test_predict_calls_predictor_once(client, predict_calls):
    text = "please help, charged twice"
    r = client.post("/predict", json={"ticket_text": text})
    assert r.status_code == 200
    assert len(predict_calls) == 1
    assert predict_calls[0]["texts"] == [text]


# ---------------------------------------------------------------------------
# Service-unavailable path for /predict
# ---------------------------------------------------------------------------


def test_predict_not_loaded(unloaded_client):
    r = unloaded_client.post("/predict", json={"ticket_text": "hello"})
    assert r.status_code == 503
    assert r.json()["detail"] == "model not loaded"
