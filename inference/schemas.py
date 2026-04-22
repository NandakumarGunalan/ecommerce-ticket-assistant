"""Pydantic request/response schemas for online inference.

Not used by the batch path — batch_predict.py operates on DB rows directly.
These are scaffolding for the future online endpoint (see inference/PLAN.md).
"""
from typing import Optional, List, Dict
from pydantic import BaseModel


class TicketItem(BaseModel):
    id: str
    ticket_text: str


class SingleTicketRequest(BaseModel):
    ticket_text: str


class BatchTicketRequest(BaseModel):
    tickets: List[TicketItem]


class Prediction(BaseModel):
    id: Optional[str] = None
    predicted_priority: str
    confidence: float
    all_scores: Dict[str, float]


class PredictionResponse(BaseModel):
    predictions: List[Prediction]
    model_version: str
    model_run_id: str
    latency_ms: int
