"""Pydantic request/response schemas for inference.

Two groupings:

- ``Prediction`` — transport-agnostic per-ticket result produced by
  :func:`inference.predictor.predict`. Used by both the batch CLI and the
  online endpoint.
- ``PredictRequest`` / ``PredictResponse`` / ``HealthResponse`` — wire-level
  contracts for the online FastAPI endpoint (see ``inference/ONLINE_PLAN.md``).

The legacy ``TicketItem`` / ``SingleTicketRequest`` / ``BatchTicketRequest`` /
``PredictionResponse`` scaffolding from the pre-online placeholder has been
removed in favor of the real online contract.
"""
from typing import Dict, Optional

from pydantic import BaseModel, Field, field_validator


class Prediction(BaseModel):
    """One scored ticket. Produced by :func:`inference.predictor.predict`."""

    id: Optional[str] = None
    predicted_priority: str
    confidence: float
    all_scores: Dict[str, float]


class PredictRequest(BaseModel):
    """Single-ticket online inference request."""

    ticket_text: str = Field(..., max_length=10_000)

    @field_validator("ticket_text")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("ticket_text must be non-empty")
        return v


class PredictResponse(BaseModel):
    """Single-ticket online inference response."""

    predicted_priority: str
    confidence: float
    all_scores: Dict[str, float]
    model_version: str
    model_run_id: str
    latency_ms: int


class HealthResponse(BaseModel):
    """Response shape for ``GET /healthz``."""

    status: str
    model_version: Optional[str] = None
    model_run_id: Optional[str] = None
