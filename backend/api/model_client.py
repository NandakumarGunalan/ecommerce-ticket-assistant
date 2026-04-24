"""HTTP client for the IAM-restricted model endpoint.

The ``distilbert-priority-online`` Cloud Run service was deployed with
``--no-allow-unauthenticated``; every request needs an ``Authorization:
Bearer <id_token>`` header where the token's ``aud`` claim equals the
service URL. On Cloud Run, ``google.oauth2.id_token.fetch_id_token``
produces such a token using the runtime service account's credentials.

We fetch tokens lazily, cache them in-process for a short TTL (default
30 minutes; Google ID tokens are valid for 60 minutes), and refresh on
expiry. This keeps per-request latency low without rolling our own OAuth
flow.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

import httpx

from backend.api.logging_utils import get_logger, log_model_endpoint_error

_LOG = get_logger(__name__)

# Re-fetch an ID token after this many seconds even if it's technically
# still valid. Google ID tokens live for 60m; 30m gives us a wide safety
# margin against clock drift between the token issuer and the callee.
_ID_TOKEN_REFRESH_SEC: float = 30 * 60


class ModelEndpointError(RuntimeError):
    """Raised when the model endpoint returns a non-200 or is unreachable.

    Carries the upstream status code (or ``None`` for network errors) so
    the HTTP handler can surface it in its 502 response body.
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class _IDTokenFetcher(Protocol):
    """Callable signature matching ``google.oauth2.id_token.fetch_id_token``.

    Declared as a Protocol so the model client can accept an injected
    fake in tests without importing google-auth.
    """

    def __call__(self, request: Any, audience: str) -> str:
        ...


@dataclass
class _CachedToken:
    value: str
    fetched_at: float


class ModelClient:
    """Thin sync wrapper around the model endpoint.

    Two modes:

    - Production: constructed with no arguments; uses
      ``google.auth.transport.requests.Request`` + ``fetch_id_token``
      against the configured ``endpoint_url``.
    - Tests: pass ``id_token_fetcher=<callable>`` (and optionally
      ``http_client=<httpx.Client>``) to swap in doubles.
    """

    def __init__(
        self,
        endpoint_url: str,
        *,
        id_token_fetcher: Optional[_IDTokenFetcher] = None,
        http_client: Optional[httpx.Client] = None,
        timeout_sec: float = 75.0,
    ) -> None:
        # Strip trailing slash so audience matches what Cloud Run signs for.
        self._endpoint_url = endpoint_url.rstrip("/")
        self._timeout = timeout_sec
        self._http_client = http_client or httpx.Client(timeout=timeout_sec)
        self._id_token_fetcher = id_token_fetcher
        self._token_lock = threading.Lock()
        self._cached: Optional[_CachedToken] = None

    # -- token handling -----------------------------------------------------

    def _resolve_fetcher(self) -> _IDTokenFetcher:
        if self._id_token_fetcher is not None:
            return self._id_token_fetcher
        # Import lazily so unit tests that inject a fake fetcher don't need
        # google-auth on their path.
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2 import id_token as _google_id_token  # type: ignore

        request = Request()

        def _fetch(req: Any, audience: str) -> str:
            # google.oauth2.id_token.fetch_id_token(request, audience) -> str
            return _google_id_token.fetch_id_token(request, audience)

        # Capture `request` in closure; the outer signature matches Protocol.
        def _fetcher(_request: Any, audience: str) -> str:
            return _fetch(request, audience)

        self._id_token_fetcher = _fetcher
        return _fetcher

    def _get_id_token(self) -> str:
        now = time.monotonic()
        with self._token_lock:
            if (
                self._cached is not None
                and now - self._cached.fetched_at < _ID_TOKEN_REFRESH_SEC
            ):
                return self._cached.value
            fetcher = self._resolve_fetcher()
            token = fetcher(None, self._endpoint_url)
            self._cached = _CachedToken(value=token, fetched_at=now)
            return token

    # -- HTTP calls ---------------------------------------------------------

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_id_token()}",
            "Content-Type": "application/json",
        }

    def healthz(self) -> Dict[str, Any]:
        url = f"{self._endpoint_url}/health"
        try:
            resp = self._http_client.get(url, headers=self._auth_headers())
        except httpx.HTTPError as exc:
            log_model_endpoint_error(
                _LOG, status_code=None, error=str(exc), endpoint=url
            )
            raise ModelEndpointError(
                f"model endpoint unreachable: {exc}", status_code=None
            ) from exc
        if resp.status_code != 200:
            log_model_endpoint_error(
                _LOG,
                status_code=resp.status_code,
                error=resp.text[:500],
                endpoint=url,
            )
            raise ModelEndpointError(
                f"model /health returned {resp.status_code}",
                status_code=resp.status_code,
            )
        return resp.json()

    def predict(self, ticket_text: str) -> Dict[str, Any]:
        url = f"{self._endpoint_url}/predict"
        try:
            resp = self._http_client.post(
                url,
                headers=self._auth_headers(),
                json={"ticket_text": ticket_text},
            )
        except httpx.HTTPError as exc:
            log_model_endpoint_error(
                _LOG, status_code=None, error=str(exc), endpoint=url
            )
            raise ModelEndpointError(
                f"model endpoint unreachable: {exc}", status_code=None
            ) from exc
        if resp.status_code != 200:
            log_model_endpoint_error(
                _LOG,
                status_code=resp.status_code,
                error=resp.text[:500],
                endpoint=url,
            )
            raise ModelEndpointError(
                f"model /predict returned {resp.status_code}: "
                f"{resp.text[:200]}",
                status_code=resp.status_code,
            )
        return resp.json()

    def close(self) -> None:
        try:
            self._http_client.close()
        except Exception:  # noqa: BLE001 — best effort
            pass
