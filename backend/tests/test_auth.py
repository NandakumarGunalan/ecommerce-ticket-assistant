"""Unit tests for ``backend.api.auth.make_current_user_dep``.

We build a tiny FastAPI app with a single route that depends on the
factory-returned dep and drive it via ``TestClient``. The real
``firebase_admin.auth.verify_id_token`` is never called — every test
injects its own stub verifier.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.api.auth import User, make_current_user_dep


def _build_app(verifier):
    app = FastAPI()
    dep = make_current_user_dep(verifier)

    @app.get("/whoami")
    async def whoami(user: User = Depends(dep)):
        return {
            "uid": user.uid,
            "email": user.email,
            "display_name": user.display_name,
        }

    return TestClient(app)


def _fail_verifier(_token: str) -> dict:  # pragma: no cover - not called
    raise AssertionError("verifier should not be called")


def test_missing_authorization_header_returns_401():
    client = _build_app(_fail_verifier)
    r = client.get("/whoami")
    assert r.status_code == 401
    assert r.json()["detail"] == "missing_bearer_token"


def test_header_without_bearer_prefix_returns_401():
    client = _build_app(_fail_verifier)
    r = client.get("/whoami", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401
    assert r.json()["detail"] == "missing_bearer_token"


def test_empty_token_returns_401():
    client = _build_app(_fail_verifier)
    r = client.get("/whoami", headers={"Authorization": "Bearer  "})
    assert r.status_code == 401
    assert r.json()["detail"] == "empty_bearer_token"


def test_verifier_raising_returns_401():
    def boom(_token: str) -> dict:
        raise ValueError("bad signature")

    client = _build_app(boom)
    r = client.get("/whoami", headers={"Authorization": "Bearer sometoken"})
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_token"


def test_claims_missing_uid_returns_401():
    client = _build_app(lambda _t: {"email": "a@b.com"})
    r = client.get("/whoami", headers={"Authorization": "Bearer t"})
    assert r.status_code == 401
    assert r.json()["detail"] == "token_missing_claims"


def test_claims_missing_email_returns_401():
    client = _build_app(lambda _t: {"uid": "u1"})
    r = client.get("/whoami", headers={"Authorization": "Bearer t"})
    assert r.status_code == 401
    assert r.json()["detail"] == "token_missing_claims"


def test_happy_path_returns_user_with_uid_email_and_name():
    claims = {"uid": "u-123", "email": "alice@example.com", "name": "Alice"}
    client = _build_app(lambda _t: claims)
    r = client.get("/whoami", headers={"Authorization": "Bearer validtoken"})
    assert r.status_code == 200
    assert r.json() == {
        "uid": "u-123",
        "email": "alice@example.com",
        "display_name": "Alice",
    }


def test_happy_path_falls_back_to_sub_when_uid_missing():
    claims = {"sub": "sub-456", "email": "bob@example.com"}
    client = _build_app(lambda _t: claims)
    r = client.get("/whoami", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200
    body = r.json()
    assert body["uid"] == "sub-456"
    assert body["email"] == "bob@example.com"
    assert body["display_name"] is None
