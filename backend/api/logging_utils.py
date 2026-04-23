"""Structured JSON logging for the backend API.

Mirrors the pattern used by ``inference/logging_utils.py``: a stdlib
``logging.Logger`` writes one JSON object per line to stdout. Cloud Run
auto-parses those into ``jsonPayload`` entries in Cloud Logging (no
``google-cloud-logging`` client required), so Logs Explorer queries like
``jsonPayload.event="ticket_created"`` work out of the box.

The payload is kept PII-light: no full ticket text — only a 100-char
``input_preview`` plus ``input_length_chars``.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, Mapping, Optional

_PAYLOAD_ATTR = "_json_payload"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {"severity": record.levelname}
        extra = getattr(record, _PAYLOAD_ATTR, None)
        if isinstance(extra, Mapping):
            payload.update(extra)
        if "event" not in payload:
            payload["message"] = record.getMessage()
        return json.dumps(payload, separators=(",", ":"), default=str)


class _DynamicStdoutHandler(logging.StreamHandler):
    """StreamHandler that re-reads ``sys.stdout`` on every emit.

    Plain ``StreamHandler(stream=sys.stdout)`` captures whatever object
    ``sys.stdout`` referenced at construction time — which, under
    pytest's output-capture plugin, gets unwrapped between tests. That
    means log lines emitted later go to a file-like object that no
    longer maps to the test's stdout capture, and ``capfd`` / ``capsys``
    don't see them. Rebinding per-emit avoids that gotcha at a cost we
    don't care about in this small service.
    """

    def __init__(self) -> None:
        super().__init__(stream=sys.stdout)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:  # type: ignore[override]
        # Ignore attempts to rebind — StreamHandler.__init__ tries to do
        # this and we want to keep deferring to sys.stdout.
        return


def get_logger(name: str) -> logging.Logger:
    """Return a stdlib logger configured to emit JSON on stdout.

    Idempotent — repeat calls with the same name won't stack handlers.
    """
    logger = logging.getLogger(name)
    if not any(getattr(h, "_backend_json", False) for h in logger.handlers):
        handler = _DynamicStdoutHandler()
        handler.setFormatter(_JsonFormatter())
        handler._backend_json = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def _emit(
    logger: logging.Logger, level: int, payload: Dict[str, Any]
) -> None:
    logger.log(level, payload.get("event", ""), extra={_PAYLOAD_ATTR: payload})


def log_ticket_created(
    logger: logging.Logger,
    *,
    ticket_id: str,
    prediction_id: str,
    predicted_priority: str,
    confidence: float,
    input_preview: str,
    input_length_chars: int,
    model_version: str,
    model_run_id: Optional[str],
    latency_ms: int,
) -> None:
    _emit(
        logger,
        logging.INFO,
        {
            "event": "ticket_created",
            "ticket_id": ticket_id,
            "prediction_id": prediction_id,
            "predicted_priority": predicted_priority,
            "confidence": confidence,
            "input_preview": input_preview,
            "input_length_chars": input_length_chars,
            "model_version": model_version,
            "model_run_id": model_run_id,
            "latency_ms": latency_ms,
        },
    )


def log_feedback_recorded(
    logger: logging.Logger,
    *,
    feedback_id: str,
    prediction_id: str,
    verdict: str,
    ticket_id: Optional[str],
    predicted_priority: Optional[str],
    confidence: Optional[float],
) -> None:
    _emit(
        logger,
        logging.INFO,
        {
            "event": "feedback_recorded",
            "feedback_id": feedback_id,
            "prediction_id": prediction_id,
            "verdict": verdict,
            "ticket_id": ticket_id,
            "predicted_priority": predicted_priority,
            "confidence": confidence,
        },
    )


def log_ticket_resolution_change(
    logger: logging.Logger,
    *,
    event: str,
    ticket_id: str,
    user_id: str,
) -> None:
    """Emit ``ticket_resolved`` / ``ticket_unresolved`` structured events."""
    _emit(
        logger,
        logging.INFO,
        {
            "event": event,
            "ticket_id": ticket_id,
            "user_id": user_id,
        },
    )


def log_model_endpoint_error(
    logger: logging.Logger,
    *,
    status_code: Optional[int],
    error: str,
    endpoint: str,
) -> None:
    _emit(
        logger,
        logging.ERROR,
        {
            "event": "model_endpoint_error",
            "status_code": status_code,
            "error": error,
            "endpoint": endpoint,
        },
    )
