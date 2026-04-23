"""FastAPI app for online prediction mode.

Single-ticket real-time inference. Loads the DistilBERT priority classifier
from Vertex AI Model Registry at startup, then serves ``POST /predict`` and
``GET /healthz`` over HTTP. See ``inference/ONLINE_PLAN.md`` for the design
and the endpoint contract.

This module reuses the shared :mod:`inference.predictor` core and the
:mod:`inference.model_loader` singleton, so batch and online share a single
inference code path.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import FastAPI, HTTPException

from inference import model_loader, predictor
from inference.logging_utils import _PAYLOAD_ATTR, get_logger
from inference.schemas import (
    HealthResponse,
    PredictRequest,
    PredictResponse,
)

logger = get_logger(__name__)

app = FastAPI(
    title="Ecommerce Ticket Assistant — Inference API",
    version="1.0.0",
)

# Module-level state flipped true after the startup hook successfully loads
# the model. Kept as a dict (not a bare bool) so tests can monkeypatch the
# entry without rebinding the module attribute.
_model_state: Dict[str, Any] = {"loaded": False}


@app.on_event("startup")
def _load_model_on_startup() -> None:
    """Load the model at container startup so the first request doesn't pay
    the download / HF-load cost.
    """
    model_loader.load_model()
    _model_state["loaded"] = True


def _log_online_prediction(payload: Dict[str, Any]) -> None:
    """Emit one ``event="online_prediction"`` JSON line via the shared
    structured-logging formatter.
    """
    logger.info(payload.get("event", ""), extra={_PAYLOAD_ATTR: payload})


@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    if not _model_state["loaded"]:
        raise HTTPException(status_code=503, detail="model not loaded")
    info = model_loader.load_model()
    return HealthResponse(
        status="ok",
        model_version=info.model_version,
        model_run_id=info.model_run_id,
    )


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse:
    if not _model_state["loaded"]:
        raise HTTPException(status_code=503, detail="model not loaded")

    info = model_loader.load_model()

    t0 = time.perf_counter()
    results = predictor.predict([req.ticket_text])
    latency_ms = int((time.perf_counter() - t0) * 1000)

    if not results:
        raise HTTPException(
            status_code=500, detail="predictor returned no results"
        )
    result = results[0]

    _log_online_prediction(
        {
            "event": "online_prediction",
            "severity": "INFO",
            "model_version": info.model_version,
            "model_run_id": info.model_run_id,
            "input_preview": req.ticket_text[:100],
            "input_length_chars": len(req.ticket_text),
            "predicted_priority": result.predicted_priority,
            "confidence": result.confidence,
            "latency_ms": latency_ms,
        }
    )

    return PredictResponse(
        predicted_priority=result.predicted_priority,
        confidence=result.confidence,
        all_scores=result.all_scores,
        model_version=info.model_version,
        model_run_id=info.model_run_id,
        latency_ms=latency_ms,
    )
