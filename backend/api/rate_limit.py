"""Per-user per-minute rate limiting.

The storage is abstracted behind a tiny ``RateLimitStore`` protocol so this
module is fully testable with an in-memory implementation. Wave 2 will add
a concrete Postgres-backed method on ``DBClient`` (token-bucket-via-counter,
one row per ``(user_id, window_start_minute)``, atomic upsert), and wire
``make_rate_limit_dep(db_client)`` into the protected endpoints.

The dep is layered on top of ``current_user_dep`` so endpoints only need a
single attachment point: depending on ``rate_limit`` yields the ``User`` and
enforces the cap in one shot.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from fastapi import Depends, HTTPException, status

from backend.api.auth import User, current_user_dep


DEFAULT_LIMIT_PER_MINUTE = 50


class RateLimitStore(Protocol):
    def increment_and_get(self, user_id: str, window_start_minute: datetime) -> int:
        """Atomically increment the counter for (user_id, window) and return new value."""
        ...


def _current_window(now: datetime | None = None) -> datetime:
    """Return the start of the current UTC minute window."""
    now = now or datetime.now(timezone.utc)
    return now.replace(second=0, microsecond=0)


def make_rate_limit_dep(
    store: RateLimitStore,
    limit: int = DEFAULT_LIMIT_PER_MINUTE,
    user_dep=current_user_dep,
):
    """Return a FastAPI dependency enforcing ``limit`` requests/user/minute.

    The returned dep depends on ``user_dep`` (defaulting to
    ``current_user_dep``) so it can be used as the sole attachment point on
    a protected endpoint. Tests can pass a stub ``user_dep`` that returns a
    fixed ``User`` without any Firebase involvement.

    On success the dep returns the authenticated ``User``; on cap breach it
    raises 429 with ``Retry-After: 60``.
    """

    async def rate_limit(user: User = Depends(user_dep)) -> User:
        window = _current_window()
        new_count = store.increment_and_get(user.uid, window)
        if new_count > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "rate_limit_exceeded",
                    "limit_per_minute": limit,
                },
                headers={"Retry-After": "60"},
            )
        return user

    return rate_limit


class InMemoryRateLimitStore:
    """Non-persistent ``RateLimitStore`` used by tests and local runs."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, datetime], int] = {}

    def increment_and_get(self, user_id: str, window_start_minute: datetime) -> int:
        key = (user_id, window_start_minute)
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]
