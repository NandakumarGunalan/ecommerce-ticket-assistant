"""Unit tests for ``backend.api.rate_limit.make_rate_limit_dep``.

Uses ``InMemoryRateLimitStore`` and a stub ``current_user_dep`` so no
Firebase is involved. The current-window clock is swapped by monkeypatching
``backend.api.rate_limit._current_window``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.api import rate_limit as rate_limit_mod
from backend.api.auth import User
from backend.api.rate_limit import (
    DEFAULT_LIMIT_PER_MINUTE,
    InMemoryRateLimitStore,
    make_rate_limit_dep,
)


def _user_dep_for(user: User):
    async def _dep() -> User:
        return user

    return _dep


def _build_app(store, user: User, limit: int = DEFAULT_LIMIT_PER_MINUTE):
    app = FastAPI()
    dep = make_rate_limit_dep(store, limit=limit, user_dep=_user_dep_for(user))

    @app.get("/ping")
    async def ping(u: User = Depends(dep)):
        return {"uid": u.uid}

    return TestClient(app)


def _fixed_clock(moment: datetime):
    def _cw(now=None):
        return moment.replace(second=0, microsecond=0)

    return _cw


def test_requests_up_to_limit_pass_and_next_is_429(monkeypatch):
    monkeypatch.setattr(
        rate_limit_mod,
        "_current_window",
        _fixed_clock(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)),
    )
    store = InMemoryRateLimitStore()
    client = _build_app(store, User(uid="u1", email="u1@x.com"))

    # First request: 200.
    assert client.get("/ping").status_code == 200

    # Fill up to the limit.
    for _ in range(DEFAULT_LIMIT_PER_MINUTE - 1):
        assert client.get("/ping").status_code == 200

    # The next one breaches the cap.
    r = client.get("/ping")
    assert r.status_code == 429


def test_429_includes_retry_after_and_detail_body(monkeypatch):
    monkeypatch.setattr(
        rate_limit_mod,
        "_current_window",
        _fixed_clock(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)),
    )
    store = InMemoryRateLimitStore()
    client = _build_app(store, User(uid="u1", email="u1@x.com"), limit=2)

    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200
    r = client.get("/ping")
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "60"
    body = r.json()
    assert body["detail"]["error"] == "rate_limit_exceeded"
    assert body["detail"]["limit_per_minute"] == 2


def test_counter_resets_across_windows(monkeypatch):
    store = InMemoryRateLimitStore()
    client = _build_app(store, User(uid="u1", email="u1@x.com"), limit=2)

    # Window A: burn through the cap.
    monkeypatch.setattr(
        rate_limit_mod,
        "_current_window",
        _fixed_clock(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)),
    )
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 429

    # Window B: new minute, counter is fresh.
    monkeypatch.setattr(
        rate_limit_mod,
        "_current_window",
        _fixed_clock(datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)),
    )
    assert client.get("/ping").status_code == 200


def test_different_users_have_independent_counters(monkeypatch):
    monkeypatch.setattr(
        rate_limit_mod,
        "_current_window",
        _fixed_clock(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)),
    )
    store = InMemoryRateLimitStore()
    user_a = User(uid="a", email="a@x.com")
    user_b = User(uid="b", email="b@x.com")
    client_a = _build_app(store, user_a)
    client_b = _build_app(store, user_b)

    # A makes 50 requests and stays at the cap (none exceed).
    for _ in range(DEFAULT_LIMIT_PER_MINUTE):
        assert client_a.get("/ping").status_code == 200

    # B makes one request — independent bucket, should be fine.
    assert client_b.get("/ping").status_code == 200

    # A's 51st still 429s — independence didn't leak the other direction either.
    assert client_a.get("/ping").status_code == 429


def test_current_window_truncates_to_minute():
    dt = datetime(2026, 4, 23, 14, 37, 42, 123456, tzinfo=timezone.utc)
    assert rate_limit_mod._current_window(dt) == datetime(
        2026, 4, 23, 14, 37, 0, 0, tzinfo=timezone.utc
    )


def test_in_memory_store_increments_per_key():
    store = InMemoryRateLimitStore()
    w1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    w2 = datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)
    assert store.increment_and_get("u", w1) == 1
    assert store.increment_and_get("u", w1) == 2
    assert store.increment_and_get("u", w2) == 1
    assert store.increment_and_get("v", w1) == 1
