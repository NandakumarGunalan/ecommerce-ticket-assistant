"""Pydantic request / response schemas for the backend API.

Shapes intentionally match the native contract of the model endpoint
(``inference/schemas.py``) so that the frontend can speak one shape
whether it calls the stateless ``/predict`` passthrough or the
persisting ``/tickets`` endpoint.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel, Field, field_validator

from backend.api.config import TICKET_TEXT_MAX_CHARS, VALID_VERDICTS

# ---------------------------------------------------------------------------
# Ticket / prediction shapes
# ---------------------------------------------------------------------------


class TicketTextRequest(BaseModel):
    """Shared request body for ``POST /predict`` and ``POST /tickets``."""

    ticket_text: str = Field(..., max_length=TICKET_TEXT_MAX_CHARS)

    @field_validator("ticket_text")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("ticket_text must be non-empty")
        return v


class PredictResponse(BaseModel):
    """Response shape for the stateless ``POST /predict`` passthrough."""

    predicted_priority: str
    confidence: float
    all_scores: Dict[str, float]
    model_version: str
    model_run_id: Optional[str] = None
    latency_ms: int


class TicketRecord(BaseModel):
    """Combined ticket + most-recent prediction record.

    Used as the response for ``POST /tickets`` and each list element of
    ``GET /tickets``. Fields that come from the ``predictions`` table are
    optional because a ticket may (in principle) exist without a
    prediction — ``GET /tickets`` surfaces ``predicted_priority: "unknown"``
    in that case rather than dropping the row.
    """

    ticket_id: str
    text: str
    prediction_id: Optional[str] = None
    predicted_priority: str
    confidence: Optional[float] = None
    all_scores: Dict[str, float] = Field(default_factory=dict)
    model_version: Optional[str] = None
    model_run_id: Optional[str] = None
    latency_ms: Optional[int] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Feedback shapes
# ---------------------------------------------------------------------------


class FeedbackRequest(BaseModel):
    """Request body for ``POST /feedback``."""

    prediction_id: str
    verdict: str
    note: Optional[str] = None

    @field_validator("verdict")
    @classmethod
    def valid_verdict(cls, v: str) -> str:
        if v not in VALID_VERDICTS:
            raise ValueError(
                f"verdict must be one of {sorted(VALID_VERDICTS)}"
            )
        return v


class FeedbackResponse(BaseModel):
    feedback_id: str
    created_at: datetime


# ---------------------------------------------------------------------------
# CSV upload
# ---------------------------------------------------------------------------


class CsvUploadResponse(BaseModel):
    """Response shape for ``POST /tickets/upload-csv``."""

    accepted: int
    skipped: int


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Response shape for ``GET /healthz`` — passthrough from the model endpoint."""

    status: str
    model_version: Optional[str] = None
    model_run_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Auth / user-profile
# ---------------------------------------------------------------------------


class MeResponse(BaseModel):
    """Response shape for ``GET /me`` — the authenticated user's profile."""

    uid: str
    email: str
    display_name: Optional[str] = None
