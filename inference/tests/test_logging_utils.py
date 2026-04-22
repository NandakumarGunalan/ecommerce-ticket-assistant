"""Unit tests for inference.logging_utils."""
from __future__ import annotations

import io
import json
import logging

import pytest

from inference.logging_utils import (
    INPUT_PREVIEW_MAX_CHARS,
    get_logger,
    log_batch_run_summary,
    log_prediction_event,
)


@pytest.fixture
def capture_logger():
    """Return (logger, buffer) where buffer receives the emitted JSON lines."""
    buf = io.StringIO()
    logger = logging.getLogger("test.inference.logging_utils")
    # Reset any handlers from a previous test run.
    logger.handlers.clear()
    # Reuse the real formatter by calling get_logger first, then redirect.
    get_logger(logger.name)
    # Swap stdout stream handler for one writing to our buffer, keeping the formatter.
    real_handler = logger.handlers[0]
    real_handler.stream = buf
    logger.propagate = False
    yield logger, buf
    logger.handlers.clear()


def _parse_single(buf: io.StringIO) -> dict:
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected 1 log line, got {len(lines)}: {lines!r}"
    return json.loads(lines[0])


def test_log_prediction_event_emits_expected_keys(capture_logger):
    logger, buf = capture_logger
    log_prediction_event(
        logger,
        run_id="pred-20260420-030000",
        model_version="2",
        model_run_id="run-20260419-140149",
        ticket_id="T-001",
        input_text="my order never arrived",
        predicted_priority="urgent",
        confidence=0.83,
        latency_ms=47.0,
    )
    event = _parse_single(buf)
    expected_keys = {
        "severity",
        "event",
        "run_id",
        "model_version",
        "model_run_id",
        "ticket_id",
        "input_preview",
        "input_length_chars",
        "predicted_priority",
        "confidence",
        "latency_ms",
    }
    assert set(event.keys()) == expected_keys
    assert event["event"] == "prediction"
    assert event["severity"] == "INFO"
    assert event["ticket_id"] == "T-001"
    assert event["predicted_priority"] == "urgent"
    assert event["confidence"] == 0.83
    assert event["input_preview"] == "my order never arrived"
    assert event["input_length_chars"] == len("my order never arrived")


def test_log_prediction_event_truncates_long_input(capture_logger):
    logger, buf = capture_logger
    long_text = "x" * 500
    log_prediction_event(
        logger,
        run_id="r",
        model_version="1",
        model_run_id="run",
        ticket_id="T-999",
        input_text=long_text,
        predicted_priority="low",
        confidence=0.5,
        latency_ms=10.0,
    )
    event = _parse_single(buf)
    assert len(event["input_preview"]) == INPUT_PREVIEW_MAX_CHARS == 100
    assert event["input_preview"] == "x" * 100
    assert event["input_length_chars"] == 500


def test_log_prediction_event_preview_matches_full_text_when_short(capture_logger):
    logger, buf = capture_logger
    short = "hi"
    log_prediction_event(
        logger,
        run_id="r",
        model_version="1",
        model_run_id="run",
        ticket_id="T-1",
        input_text=short,
        predicted_priority="low",
        confidence=0.9,
        latency_ms=1.0,
    )
    event = _parse_single(buf)
    assert event["input_preview"] == short
    assert event["input_length_chars"] == 2


def test_log_batch_run_summary_emits_expected_keys(capture_logger):
    logger, buf = capture_logger
    log_batch_run_summary(
        logger,
        run_id="pred-20260420-030000",
        model_version="2",
        model_run_id="run-20260419-140149",
        selection_mode="default",
        row_count=1247,
        row_count_scored=1243,
        row_count_skipped=4,
        class_distribution={"low": 182, "medium": 432, "high": 498, "urgent": 131},
        avg_confidence=0.72,
        runtime_sec=47.3,
        started_at="2026-04-20T03:00:00Z",
        finished_at="2026-04-20T03:00:47Z",
    )
    event = _parse_single(buf)
    expected_keys = {
        "severity",
        "event",
        "run_id",
        "model_version",
        "model_run_id",
        "selection_mode",
        "row_count",
        "row_count_scored",
        "row_count_skipped",
        "class_distribution",
        "avg_confidence",
        "runtime_sec",
        "started_at",
        "finished_at",
    }
    assert set(event.keys()) == expected_keys
    assert event["event"] == "batch_run_summary"
    assert event["severity"] == "INFO"
    assert event["class_distribution"] == {
        "low": 182,
        "medium": 432,
        "high": 498,
        "urgent": 131,
    }
    assert event["row_count"] == 1247
    assert event["row_count_scored"] == 1243
    assert event["row_count_skipped"] == 4
