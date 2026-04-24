"""Tests for ``GET /me`` — the authenticated-profile echo endpoint."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.main import app, get_db, get_model_client


def test_me_happy_path(client, stub_user):
    """Authed caller gets their uid/email/display_name back."""
    r = client.get("/me")
    assert r.status_code == 200
    assert r.json() == {
        "uid": stub_user.uid,
        "email": stub_user.email,
        "display_name": stub_user.display_name,
    }


def test_me_requires_auth(db, model_stub):
    """No ``current_user_dep`` override → 401 (no bearer token)."""
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_model_client] = lambda: model_stub
    try:
        client = TestClient(app)
        r = client.get("/me")
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()
