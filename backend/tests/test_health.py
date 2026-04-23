"""Tests for ``GET /healthz``."""
from __future__ import annotations

from backend.api.model_client import ModelEndpointError


def test_healthz_passthrough(client, model_stub):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "status": "ok",
        "model_version": "2",
        "model_run_id": "run-test",
    }
    assert len(model_stub.healthz_calls) == 1


def test_healthz_model_unreachable(client, model_stub):
    model_stub.healthz_error = ModelEndpointError(
        "boom", status_code=None
    )
    r = client.get("/healthz")
    assert r.status_code == 502
    assert r.json()["detail"]["error"] == "model_endpoint_error"
