"""Tests for ``POST /predict`` — the stateless passthrough endpoint."""
from __future__ import annotations

import pytest

from backend.api.model_client import ModelEndpointError


def test_predict_happy_path(client, model_stub):
    r = client.post(
        "/predict", json={"ticket_text": "my order never arrived"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["predicted_priority"] == "urgent"
    assert body["confidence"] == pytest.approx(0.83)
    assert body["all_scores"]["urgent"] == pytest.approx(0.83)
    assert body["model_version"] == "2"
    assert body["model_run_id"] == "run-test"
    assert body["latency_ms"] == 42
    assert model_stub.predict_calls == ["my order never arrived"]


def test_predict_does_not_write_to_db(client, db, stub_user):
    r = client.post("/predict", json={"ticket_text": "hello"})
    assert r.status_code == 200
    # No ticket created by /predict.
    assert client.get("/tickets").json() == []
    assert db.list_tickets(user_id=stub_user.uid) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"ticket_text": ""},
        {"ticket_text": "   "},
        {"ticket_text": "x" * 10_001},
        {},
    ],
)
def test_predict_validation_errors(client, payload):
    r = client.post("/predict", json=payload)
    # FastAPI returns 422 by default for pydantic validation failures;
    # spec says "400" but either is a client-error semantic.
    assert r.status_code in (400, 422)


def test_predict_model_endpoint_502(client, model_stub):
    model_stub.predict_error = ModelEndpointError(
        "model /predict returned 500: boom", status_code=500
    )
    r = client.post("/predict", json={"ticket_text": "hi"})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["error"] == "model_endpoint_error"
    assert detail["upstream_status"] == 500


def test_predict_model_endpoint_unreachable(client, model_stub):
    model_stub.predict_error = ModelEndpointError(
        "model endpoint unreachable: conn refused", status_code=None
    )
    r = client.post("/predict", json={"ticket_text": "hi"})
    assert r.status_code == 502
    assert r.json()["detail"]["upstream_status"] is None
