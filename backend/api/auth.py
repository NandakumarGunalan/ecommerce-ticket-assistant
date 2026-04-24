"""Firebase ID-token verification for FastAPI.

This module is designed to be test-friendly: the real
``firebase_admin.auth.verify_id_token`` call is wrapped in a tiny
``_default_verifier`` function, and ``make_current_user_dep`` accepts any
callable with the same shape. Tests can inject a stub verifier without
touching Firebase at all.

Wave 1 note: nothing in this module is wired into ``main.py`` yet. Wave 2
will import ``current_user_dep`` (or call ``make_current_user_dep``) and
attach it to protected endpoints, and will call ``init_firebase_app`` at
startup.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import Header, HTTPException, status


@dataclass(frozen=True)
class User:
    uid: str
    email: str
    display_name: Optional[str] = None


# Type for any function that takes a raw ID token and returns a dict of decoded claims.
# Real impl: firebase_auth.verify_id_token. Test impl: a stub that returns a fixed dict.
TokenVerifier = Callable[[str], dict]


_firebase_initialized = False


def init_firebase_app() -> None:
    """Initialize the firebase_admin app once. Safe to call multiple times.

    Imports ``firebase_admin`` lazily so that importing this module (e.g. in
    tests that stub out the verifier) does not require the package to be
    importable or credentials to be available.
    """
    global _firebase_initialized
    if _firebase_initialized:
        return
    import firebase_admin  # lazy import

    if not firebase_admin._apps:
        firebase_admin.initialize_app()  # uses ADC / default credentials
    _firebase_initialized = True


def _default_verifier(token: str) -> dict:
    # Lazy import so tests that use a stub verifier don't need firebase_admin
    # initialized or even importable at module load.
    from firebase_admin import auth as firebase_auth

    return firebase_auth.verify_id_token(token)


def make_current_user_dep(verifier: TokenVerifier = _default_verifier):
    """Return a FastAPI dependency that verifies the bearer token and returns a User.

    Separating the factory from the dep lets tests call
    ``make_current_user_dep(stub)`` and either use the returned dep directly
    or override it with ``app.dependency_overrides``.
    """

    async def current_user(authorization: str = Header(default="")) -> User:
        if not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing_bearer_token",
            )
        token = authorization[len("Bearer "):].strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="empty_bearer_token",
            )
        try:
            claims = verifier(token)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_token",
            )
        uid = claims.get("uid") or claims.get("sub")
        email = claims.get("email")
        if not uid or not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="token_missing_claims",
            )
        return User(uid=uid, email=email, display_name=claims.get("name"))

    return current_user


# Convenience: the default dep uses the real verifier.
# Wave 2's main.py will import this: `from backend.api.auth import current_user_dep`
current_user_dep = make_current_user_dep()
