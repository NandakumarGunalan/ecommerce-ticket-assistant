"""FastAPI application for the ticket-backend-api service.

Wire-up:

- At startup, build a :class:`~backend.api.model_client.ModelClient`
  pointed at ``MODEL_ENDPOINT_URL`` and a production Postgres client
  (via :func:`~backend.api.db_client.build_postgres_client_from_env`).
- Endpoints look up those singletons via FastAPI's dependency-injection
  overrides, which is what the test suite uses to swap in fakes
  (:class:`InMemoryDBClient`, a stub model client) without touching the
  network or GCP SDKs.

The service is deployed as a **public** Cloud Run service (no-auth) —
the frontend hits it directly. When the frontend is locked down behind
its own origin this CORS policy should be tightened; see the comment
around :func:`_install_cors`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.api import config, logging_utils
from backend.api.auth import User, current_user_dep, init_firebase_app
from backend.api.db_client import (
    DBClient,
    TicketPredictionRecord,
    build_postgres_client_from_env,
)
from backend.api.logging_utils import get_logger
from backend.api.model_client import ModelClient, ModelEndpointError
from backend.api.rate_limit import make_rate_limit_dep
from backend.api.schemas import (
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    MeResponse,
    PredictResponse,
    TicketRecord,
    TicketTextRequest,
)

_LOG = get_logger(__name__)


# ---------------------------------------------------------------------------
# App + CORS
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Ecommerce Ticket Assistant — Backend API",
    version="1.0.0",
)


def _install_cors(application: FastAPI) -> None:
    """Lock CORS down to the deployed frontend + local dev origins.

    With bearer-token auth in play, wildcards are no longer acceptable:
    the browser must send ``Authorization`` from a known origin.
    """
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://ticket-frontend-48533944424.us-central1.run.app",
            # Firebase Hosting origins — same-origin as the Firebase auth
            # handler (/__/auth/handler), which avoids the cross-site
            # storage partitioning that broke signInWithRedirect on
            # the Cloud Run origin.
            "https://msds-603-victors-demons.web.app",
            "https://msds-603-victors-demons.firebaseapp.com",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )


_install_cors(app)


# ---------------------------------------------------------------------------
# Dependency singletons
# ---------------------------------------------------------------------------
#
# We use plain module-level holders + override-friendly dependency
# callables (rather than FastAPI's `app.state`) because the test harness
# overrides dependencies with `app.dependency_overrides[...]`.

_state: Dict[str, Any] = {"db": None, "model": None}


def get_db() -> DBClient:
    db = _state["db"]
    if db is None:
        raise HTTPException(status_code=503, detail="db not initialized")
    return db


def rate_limited_user(
    user: User = Depends(current_user_dep),
    db: DBClient = Depends(get_db),
) -> User:
    """Authenticate + rate-limit the caller in a single attachment point.

    Mirrors the behavior of
    :func:`backend.api.rate_limit.make_rate_limit_dep` but resolves the
    store via ``Depends(get_db)`` so ``app.dependency_overrides[get_db]``
    in tests transparently swaps the rate-limit backend too. Building
    ``make_rate_limit_dep`` at startup would bind the store to the
    production DB client and defeat those overrides.
    """
    from backend.api.rate_limit import (
        DEFAULT_LIMIT_PER_MINUTE,
        _current_window,
    )

    window = _current_window()
    new_count = db.increment_and_get(user.uid, window)
    if new_count > DEFAULT_LIMIT_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "limit_per_minute": DEFAULT_LIMIT_PER_MINUTE,
            },
            headers={"Retry-After": "60"},
        )
    return user


def get_model_client() -> ModelClient:
    m = _state["model"]
    if m is None:
        raise HTTPException(
            status_code=503, detail="model client not initialized"
        )
    return m


@app.on_event("startup")
def _startup() -> None:
    """Build real clients when running under uvicorn/Cloud Run.

    Tests override the dependency callables before sending requests, so
    they never enter this path — but the startup hook still fires when
    they use ``TestClient(app)`` as a context manager. Guard with
    ``_state`` so re-initialization in tests is a no-op.
    """
    if _state["model"] is None:
        endpoint_url = config.require_env(config.MODEL_ENDPOINT_URL_ENV)
        _state["model"] = ModelClient(endpoint_url=endpoint_url)
    if _state["db"] is None:
        _state["db"] = build_postgres_client_from_env()
    # Firebase app must be initialized before the first request so the
    # default verifier in ``current_user_dep`` can call
    # ``firebase_admin.auth.verify_id_token``. Safe to call multiple
    # times; no-op after the first.
    init_firebase_app()


@app.on_event("shutdown")
def _shutdown() -> None:
    db = _state.get("db")
    if db is not None:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
    model = _state.get("model")
    if model is not None:
        try:
            model.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_to_schema(record: TicketPredictionRecord) -> TicketRecord:
    """Convert the internal DTO to the wire-level pydantic model.

    Missing prediction fields collapse to ``predicted_priority="unknown"``
    with empty ``all_scores`` — per spec, this only happens if the data
    is dirty (normal writes go through ``insert_ticket_and_prediction``
    which populates both).
    """
    return TicketRecord(
        ticket_id=record.ticket_id,
        text=record.text,
        prediction_id=record.prediction_id,
        predicted_priority=record.predicted_priority or "unknown",
        confidence=record.confidence,
        all_scores=record.all_scores or {},
        model_version=record.model_version,
        model_run_id=record.model_run_id,
        latency_ms=record.latency_ms,
        created_at=record.created_at,
    )


def _call_model_or_502(
    model: ModelClient, ticket_text: str
) -> Dict[str, Any]:
    """Call the model endpoint, translating any failure to a 502.

    The model client has already logged a ``model_endpoint_error`` at
    this point, so we don't re-log here.
    """
    try:
        return model.predict(ticket_text)
    except ModelEndpointError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "model_endpoint_error",
                "message": str(exc),
                "upstream_status": exc.status_code,
            },
        ) from exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/healthz", response_model=HealthResponse)
@app.get("/health", response_model=HealthResponse)
def healthz(
    model: ModelClient = Depends(get_model_client),
) -> HealthResponse:
    """Passthrough to the model endpoint's ``/healthz``.

    Registered at BOTH ``/healthz`` and ``/health``: Cloud Run's ingress
    intercepts ``/healthz`` for some service configurations and never
    forwards the request to the container (returns Google's generic
    HTML 404 instead of FastAPI's JSON 404). ``/health`` is the alias
    the frontend / smoke tests should use; ``/healthz`` remains for
    callers that already bind to the kubernetes-idiomatic path.
    """
    try:
        info = model.healthz()
    except ModelEndpointError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "model_endpoint_error",
                "message": str(exc),
                "upstream_status": exc.status_code,
            },
        ) from exc
    return HealthResponse(
        status=info.get("status", "ok"),
        model_version=info.get("model_version"),
        model_run_id=info.get("model_run_id"),
    )


@app.post("/predict", response_model=PredictResponse)
def predict(
    req: TicketTextRequest,
    model: ModelClient = Depends(get_model_client),
    user: User = Depends(rate_limited_user),
) -> PredictResponse:
    """Stateless passthrough — no DB write.

    Kept so the frontend can score text without committing to persisting
    it (e.g. a "preview" flow). For the usual flow, call ``POST /tickets``.
    """
    body = _call_model_or_502(model, req.ticket_text)
    return PredictResponse(
        predicted_priority=body["predicted_priority"],
        confidence=body["confidence"],
        all_scores=body["all_scores"],
        model_version=body["model_version"],
        model_run_id=body.get("model_run_id"),
        latency_ms=body["latency_ms"],
    )


@app.post("/tickets", response_model=TicketRecord)
def create_ticket(
    req: TicketTextRequest,
    model: ModelClient = Depends(get_model_client),
    db: DBClient = Depends(get_db),
    user: User = Depends(rate_limited_user),
) -> TicketRecord:
    """Score + persist + return the combined record."""
    body = _call_model_or_502(model, req.ticket_text)
    record = db.insert_ticket_and_prediction(
        user_id=user.uid,
        ticket_text=req.ticket_text,
        predicted_priority=body["predicted_priority"],
        confidence=float(body["confidence"]),
        all_scores=body["all_scores"],
        model_version=body["model_version"],
        model_run_id=body.get("model_run_id"),
        latency_ms=int(body["latency_ms"]),
    )

    logging_utils.log_ticket_created(
        _LOG,
        ticket_id=record.ticket_id,
        prediction_id=record.prediction_id or "",
        predicted_priority=record.predicted_priority or "",
        confidence=record.confidence or 0.0,
        input_preview=req.ticket_text[: config.INPUT_PREVIEW_MAX_CHARS],
        input_length_chars=len(req.ticket_text),
        model_version=record.model_version or "",
        model_run_id=record.model_run_id,
        latency_ms=record.latency_ms or 0,
    )

    return _record_to_schema(record)


@app.get("/tickets", response_model=List[TicketRecord])
def list_tickets(
    limit: int = Query(default=50, ge=1, le=500),
    db: DBClient = Depends(get_db),
    user: User = Depends(rate_limited_user),
) -> List[TicketRecord]:
    records = db.list_tickets(user_id=user.uid, limit=limit)
    return [_record_to_schema(r) for r in records]


@app.post("/feedback", response_model=FeedbackResponse)
def create_feedback(
    req: FeedbackRequest,
    db: DBClient = Depends(get_db),
    user: User = Depends(rate_limited_user),
) -> FeedbackResponse:
    try:
        feedback_id, created_at = db.insert_feedback(
            prediction_id=req.prediction_id,
            user_id=user.uid,
            verdict=req.verdict,
            note=req.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Best-effort enrich for the log line: if the db exposes
    # get_prediction_context (InMemoryDBClient does; PostgresDBClient
    # doesn't), use it. Otherwise just log the core fields.
    ticket_id: Optional[str] = None
    predicted_priority: Optional[str] = None
    confidence: Optional[float] = None
    get_ctx = getattr(db, "get_prediction_context", None)
    if callable(get_ctx):
        ctx = get_ctx(req.prediction_id)
        if ctx is not None:
            ticket_id = ctx.get("ticket_id")
            predicted_priority = ctx.get("predicted_priority")
            confidence = ctx.get("confidence")

    logging_utils.log_feedback_recorded(
        _LOG,
        feedback_id=feedback_id,
        prediction_id=req.prediction_id,
        verdict=req.verdict,
        ticket_id=ticket_id,
        predicted_priority=predicted_priority,
        confidence=confidence,
    )

    return FeedbackResponse(feedback_id=feedback_id, created_at=created_at)


@app.get("/me", response_model=MeResponse)
def me(user: User = Depends(rate_limited_user)) -> MeResponse:
    """Return the authenticated caller's profile.

    Useful for the frontend to round-trip the decoded token claims — the
    client can confirm it's talking to the right backend and surface the
    uid/email/display_name without re-decoding the JWT.
    """
    return MeResponse(
        uid=user.uid,
        email=user.email,
        display_name=user.display_name,
    )
