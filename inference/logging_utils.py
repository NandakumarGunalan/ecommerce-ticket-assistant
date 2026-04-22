"""Structured JSON logging for inference.

Cloud Run auto-parses JSON written to stdout into Cloud Logging's
`jsonPayload` (detected via the `K_SERVICE` env var being set in the
managed runtime). So we just need a stdlib `logging.Logger` with a JSON
formatter on stdout — no `google-cloud-logging` dependency required.

Two event shapes are emitted:

- ``event="prediction"`` — one per scored ticket (or batched group),
  with `input_preview` truncated to 100 chars to avoid logging PII at scale.
- ``event="batch_run_summary"`` — one per batch job invocation, with
  aggregate counts / class distribution / runtime.

Both shapes are defined in ``inference/PLAN.md`` under
"Structured Prediction Logging" and the batch CLI spec.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, Mapping

# Max chars of input text to log. Anything longer is truncated to this length.
INPUT_PREVIEW_MAX_CHARS: int = 100

# Fields that, when set on a LogRecord via `extra={...}`, get merged into the
# emitted JSON payload at the top level. Anything else on the record is ignored
# by the formatter.
_PAYLOAD_ATTR = "_json_payload"


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line on stdout.

    If the record carries a structured payload (via ``extra={_PAYLOAD_ATTR: {...}}``),
    those keys are emitted at the top level of the JSON object alongside
    ``severity`` and ``message``. Otherwise a minimal ``{severity, message}``
    object is emitted.
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: Dict[str, Any] = {"severity": record.levelname}
        extra = getattr(record, _PAYLOAD_ATTR, None)
        if isinstance(extra, Mapping):
            payload.update(extra)
        # Only add the human message if there isn't already an `event` key
        # (structured events are self-describing; plain log lines aren't).
        if "event" not in payload:
            payload["message"] = record.getMessage()
        return json.dumps(payload, separators=(",", ":"), default=str)


def get_logger(name: str) -> logging.Logger:
    """Return a stdlib logger configured to emit JSON on stdout.

    Idempotent — calling twice with the same name will not stack handlers.
    """
    logger = logging.getLogger(name)
    if not any(getattr(h, "_inference_json", False) for h in logger.handlers):
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(_JsonFormatter())
        # Marker so we don't re-add on repeated calls.
        handler._inference_json = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    # Don't double-emit via the root logger's handlers.
    logger.propagate = False
    return logger


def _emit(logger: logging.Logger, level: int, payload: Dict[str, Any]) -> None:
    logger.log(level, payload.get("event", ""), extra={_PAYLOAD_ATTR: payload})


def log_prediction_event(
    logger: logging.Logger,
    *,
    run_id: str,
    model_version: str,
    model_run_id: str,
    ticket_id: str,
    input_text: str,
    predicted_priority: str,
    confidence: float,
    latency_ms: float,
) -> None:
    """Emit one ``event="prediction"`` JSON line.

    ``input_text`` is not logged in full. ``input_preview`` is the first
    ``INPUT_PREVIEW_MAX_CHARS`` characters; ``input_length_chars`` is the
    original length in characters.
    """
    payload: Dict[str, Any] = {
        "event": "prediction",
        "run_id": run_id,
        "model_version": model_version,
        "model_run_id": model_run_id,
        "ticket_id": ticket_id,
        "input_preview": input_text[:INPUT_PREVIEW_MAX_CHARS],
        "input_length_chars": len(input_text),
        "predicted_priority": predicted_priority,
        "confidence": confidence,
        "latency_ms": latency_ms,
    }
    _emit(logger, logging.INFO, payload)


def log_batch_run_summary(
    logger: logging.Logger,
    *,
    run_id: str,
    model_version: str,
    model_run_id: str,
    selection_mode: str,
    row_count: int,
    row_count_scored: int,
    row_count_skipped: int,
    class_distribution: Mapping[str, int],
    avg_confidence: float,
    runtime_sec: float,
    started_at: str,
    finished_at: str,
) -> None:
    """Emit one ``event="batch_run_summary"`` JSON line."""
    payload: Dict[str, Any] = {
        "event": "batch_run_summary",
        "run_id": run_id,
        "model_version": model_version,
        "model_run_id": model_run_id,
        "selection_mode": selection_mode,
        "row_count": row_count,
        "row_count_scored": row_count_scored,
        "row_count_skipped": row_count_skipped,
        "class_distribution": dict(class_distribution),
        "avg_confidence": avg_confidence,
        "runtime_sec": runtime_sec,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    _emit(logger, logging.INFO, payload)
