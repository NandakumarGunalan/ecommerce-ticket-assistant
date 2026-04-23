"""Shared fixtures for backend API tests.

The strategy: never hit a real Postgres or the real model endpoint.

- ``db`` is an :class:`InMemoryDBClient` — a dict-backed stand-in with
  the same public surface as the production Postgres client.
- ``model_stub`` is a small in-memory fake of the model client whose
  behavior tests can program per-case (next response, or raise).
- ``client`` is a FastAPI ``TestClient`` with the two dependencies
  injected via ``app.dependency_overrides`` so no startup hook runs.

Tests that want to assert on structured log output capture stdout via
``capsys``; the log formatter writes JSON lines to stdout (see
:mod:`backend.api.logging_utils`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from backend.api.auth import User, current_user_dep
from backend.api.db_client import InMemoryDBClient
from backend.api.model_client import ModelEndpointError
from backend.api.main import app, get_db, get_model_client


@dataclass
class StubModelClient:
    """Programmable fake for :class:`backend.api.model_client.ModelClient`.

    Set ``predict_response`` to a dict to have ``.predict`` return it;
    set ``predict_error`` to a :class:`ModelEndpointError` to have it
    raise instead. Same knobs exist for ``healthz``.

    All calls are recorded in ``predict_calls`` / ``healthz_calls`` so
    tests can assert on input shape / count.
    """

    predict_response: Optional[Dict[str, Any]] = None
    predict_error: Optional[ModelEndpointError] = None
    healthz_response: Optional[Dict[str, Any]] = None
    healthz_error: Optional[ModelEndpointError] = None
    predict_calls: List[str] = field(default_factory=list)
    healthz_calls: List[bool] = field(default_factory=list)

    def predict(self, ticket_text: str) -> Dict[str, Any]:
        self.predict_calls.append(ticket_text)
        if self.predict_error is not None:
            raise self.predict_error
        assert self.predict_response is not None, (
            "StubModelClient: set predict_response before calling .predict"
        )
        return self.predict_response

    def healthz(self) -> Dict[str, Any]:
        self.healthz_calls.append(True)
        if self.healthz_error is not None:
            raise self.healthz_error
        assert self.healthz_response is not None, (
            "StubModelClient: set healthz_response before calling .healthz"
        )
        return self.healthz_response

    def close(self) -> None:  # pragma: no cover — no-op for fakes
        return None


def _default_predict_response() -> Dict[str, Any]:
    return {
        "predicted_priority": "urgent",
        "confidence": 0.83,
        "all_scores": {
            "low": 0.02,
            "medium": 0.05,
            "high": 0.10,
            "urgent": 0.83,
        },
        "model_version": "2",
        "model_run_id": "run-test",
        "latency_ms": 42,
    }


def _default_healthz_response() -> Dict[str, Any]:
    return {
        "status": "ok",
        "model_version": "2",
        "model_run_id": "run-test",
    }


@pytest.fixture
def db() -> InMemoryDBClient:
    return InMemoryDBClient()


@pytest.fixture
def stub_user() -> User:
    """Default authenticated user for tests that don't care about identity."""
    return User(
        uid="test-user-1",
        email="test@example.com",
        display_name="Test User",
    )


@pytest.fixture
def model_stub() -> StubModelClient:
    return StubModelClient(
        predict_response=_default_predict_response(),
        healthz_response=_default_healthz_response(),
    )


@pytest.fixture
def client(
    db: InMemoryDBClient,
    model_stub: StubModelClient,
    stub_user: User,
) -> TestClient:
    """TestClient with external deps overridden, auth stubbed.

    We do NOT enter the TestClient as a context manager here — that
    would trigger the ``@app.on_event("startup")`` hook which tries to
    build a real ModelClient, contact Secret Manager, and initialize
    Firebase. Dependency overrides plus a bare TestClient are enough to
    exercise every handler.

    ``current_user_dep`` is overridden to return ``stub_user`` so every
    request authenticates as the fixed identity without a Firebase token.
    The rate-limit dep is NOT overridden — it uses ``db.increment_and_get``,
    and ``InMemoryDBClient``'s counter resets per-test (fresh ``db``
    fixture) and won't trip the 50/minute cap in any normal suite.
    """
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_model_client] = lambda: model_stub
    app.dependency_overrides[current_user_dep] = lambda: stub_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# Re-export so tests can build their own variants without repeating the
# default shapes.
default_predict_response: Callable[[], Dict[str, Any]] = _default_predict_response
default_healthz_response: Callable[[], Dict[str, Any]] = _default_healthz_response
