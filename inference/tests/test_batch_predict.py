"""Unit tests for ``inference.batch_predict``.

These exercise the batch runner end-to-end with:

- A ``FakeDBClient`` that returns a canned iterator of ``TicketRow`` objects
  and records every ``insert_predictions`` call.
- A fake ``predict_fn`` that returns one :class:`inference.schemas.Prediction`
  per input text, cycling through a canned priority sequence so tests can
  assert class distribution / confidence aggregates.
- A stub ``load_model_fn`` that returns a ``SimpleNamespace`` carrying a
  fixed ``model_version`` / ``model_run_id``.

No real model load, no real DB — the whole flow runs in-process in a few ms.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Iterable, Iterator, List, Optional

import pytest

from inference import config
from inference.batch_predict import BatchDeps, parse_args, run_batch
from inference.db import PredictionRow, TicketRow
from inference.schemas import Prediction


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeDBClient:
    """Records fetch args and insert calls; yields canned rows."""

    rows: List[TicketRow] = field(default_factory=list)
    fetch_calls: List[dict] = field(default_factory=list)
    insert_calls: List[dict] = field(default_factory=list)
    closed: bool = False

    def fetch_unscored_tickets(
        self,
        model_version: str,
        *,
        since: Optional[str] = None,
        ticket_ids: Optional[List[str]] = None,
        batch_fetch_size: int = 1000,
    ) -> Iterator[TicketRow]:
        self.fetch_calls.append(
            {
                "model_version": model_version,
                "since": since,
                "ticket_ids": ticket_ids,
                "batch_fetch_size": batch_fetch_size,
            }
        )
        for r in self.rows:
            yield r

    def insert_predictions(
        self, predictions: Iterable[PredictionRow], *, overwrite: bool = False
    ) -> int:
        rows = list(predictions)
        self.insert_calls.append({"rows": rows, "overwrite": overwrite})
        return len(rows)

    def close(self) -> None:
        self.closed = True


def _make_predict_fn(priorities: List[str], confidences: Optional[List[float]] = None):
    """Build a fake predict_fn cycling through `priorities`/`confidences`."""
    call_log: List[dict] = []

    def predict_fn(texts, ids=None, *, batch_size: int = 32):
        call_log.append({"texts": list(texts), "ids": list(ids) if ids else None, "batch_size": batch_size})
        out = []
        for i, tid in enumerate(ids if ids is not None else [None] * len(texts)):
            p = priorities[(len(call_log) - 1) * 0 + i] if False else priorities[
                # Flat-index across the whole test run:
                (sum(len(c["texts"]) for c in call_log[:-1]) + i) % len(priorities)
            ]
            conf = (
                confidences[
                    (sum(len(c["texts"]) for c in call_log[:-1]) + i) % len(confidences)
                ]
                if confidences
                else 0.9
            )
            out.append(
                Prediction(
                    id=tid,
                    predicted_priority=p,
                    confidence=conf,
                    all_scores={"low": 0.1, "medium": 0.2, "high": 0.3, "urgent": 0.4},
                )
            )
        return out

    predict_fn.call_log = call_log  # type: ignore[attr-defined]
    return predict_fn


def _stub_load_model(model_version: str = "1", model_run_id: str = "run-test") -> Any:
    return lambda: SimpleNamespace(
        model=None, tokenizer=None, model_version=model_version, model_run_id=model_run_id
    )


def _build_deps(db: FakeDBClient, predict_fn, model_version: str = "1") -> BatchDeps:
    return BatchDeps(
        db_client_factory=lambda: db,
        load_model_fn=_stub_load_model(model_version=model_version),
        predict_fn=predict_fn,
    )


def _args(**overrides):
    defaults = dict(
        since=None,
        ticket_ids=None,
        model_version=None,
        run_id="test-run-id",
        batch_size=32,
        db_fetch_size=1000,
        dry_run=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_parse_args_mutually_exclusive_since_and_ticket_ids():
    # argparse exits the process on mutually-exclusive violation; catch that.
    with pytest.raises(SystemExit):
        parse_args(["--since", "2026-04-19", "--ticket-ids", "T-001"])


def test_parse_args_defaults():
    ns = parse_args([])
    assert ns.since is None
    assert ns.ticket_ids is None
    assert ns.batch_size == 32
    assert ns.db_fetch_size == 1000
    assert ns.dry_run is False


def test_happy_path_five_tickets():
    rows = [TicketRow(f"T-{i}", f"text {i}") for i in range(5)]
    db = FakeDBClient(rows=rows)
    predict_fn = _make_predict_fn(["low", "medium", "high", "urgent", "medium"])

    summary = run_batch(_args(), deps=_build_deps(db, predict_fn))

    assert summary["row_count"] == 5
    assert summary["row_count_scored"] == 5
    assert summary["row_count_skipped"] == 0
    assert len(db.insert_calls) == 1
    assert len(db.insert_calls[0]["rows"]) == 5
    assert db.closed is True


def test_empty_selection():
    db = FakeDBClient(rows=[])
    predict_fn = _make_predict_fn(["low"])

    summary = run_batch(_args(), deps=_build_deps(db, predict_fn))

    assert summary["row_count"] == 0
    assert summary["row_count_scored"] == 0
    assert summary["row_count_skipped"] == 0
    assert len(db.insert_calls) == 0
    assert predict_fn.call_log == []  # type: ignore[attr-defined]


def test_malformed_row_is_skipped():
    rows = [
        TicketRow("T-1", "good text"),
        TicketRow("T-2", ""),  # empty -> skip
        TicketRow("T-3", "more text"),
    ]
    db = FakeDBClient(rows=rows)
    predict_fn = _make_predict_fn(["low", "medium"])

    summary = run_batch(_args(), deps=_build_deps(db, predict_fn))

    assert summary["row_count"] == 3
    assert summary["row_count_scored"] == 2
    assert summary["row_count_skipped"] == 1
    # predict got 2 texts (not 3)
    assert len(predict_fn.call_log) == 1  # type: ignore[attr-defined]
    assert predict_fn.call_log[0]["texts"] == ["good text", "more text"]  # type: ignore[attr-defined]
    assert len(db.insert_calls) == 1
    assert len(db.insert_calls[0]["rows"]) == 2


def test_malformed_row_none_text_is_skipped():
    rows = [
        TicketRow("T-1", "ok"),
        TicketRow("T-2", None),  # null
    ]
    db = FakeDBClient(rows=rows)
    predict_fn = _make_predict_fn(["low"])

    summary = run_batch(_args(), deps=_build_deps(db, predict_fn))

    assert summary["row_count_skipped"] == 1
    assert summary["row_count_scored"] == 1


def test_batching_100_tickets_batch_size_32():
    rows = [TicketRow(f"T-{i}", f"text {i}") for i in range(100)]
    db = FakeDBClient(rows=rows)
    predict_fn = _make_predict_fn(["low"])

    summary = run_batch(_args(batch_size=32), deps=_build_deps(db, predict_fn))

    # 32 + 32 + 32 + 4 = 100 -> 4 predict calls, 4 insert calls.
    assert len(predict_fn.call_log) == 4  # type: ignore[attr-defined]
    sizes = [len(c["texts"]) for c in predict_fn.call_log]  # type: ignore[attr-defined]
    assert sizes == [32, 32, 32, 4]
    assert len(db.insert_calls) == 4
    assert summary["row_count_scored"] == 100


def test_dry_run_skips_insert():
    rows = [TicketRow(f"T-{i}", f"text {i}") for i in range(3)]
    db = FakeDBClient(rows=rows)
    predict_fn = _make_predict_fn(["low", "medium", "high"])

    summary = run_batch(_args(dry_run=True), deps=_build_deps(db, predict_fn))

    assert summary["row_count_scored"] == 3
    assert summary["selection_mode"] == "dry-run"
    assert len(db.insert_calls) == 0


def test_explicit_ticket_ids_triggers_upsert():
    rows = [TicketRow("T-1", "a"), TicketRow("T-2", "b")]
    db = FakeDBClient(rows=rows)
    predict_fn = _make_predict_fn(["low"])

    summary = run_batch(
        _args(ticket_ids="T-1,T-2"),
        deps=_build_deps(db, predict_fn),
    )

    assert summary["selection_mode"] == "explicit-ids"
    assert db.fetch_calls[0]["ticket_ids"] == ["T-1", "T-2"]
    assert db.fetch_calls[0]["since"] is None
    assert len(db.insert_calls) == 1
    assert db.insert_calls[0]["overwrite"] is True


def test_since_mode_passes_since_and_insert_no_overwrite():
    rows = [TicketRow("T-1", "a")]
    db = FakeDBClient(rows=rows)
    predict_fn = _make_predict_fn(["low"])

    summary = run_batch(
        _args(since="2026-04-19"),
        deps=_build_deps(db, predict_fn),
    )

    assert summary["selection_mode"] == "since"
    assert db.fetch_calls[0]["since"] == "2026-04-19"
    assert db.fetch_calls[0]["ticket_ids"] is None
    assert db.insert_calls[0]["overwrite"] is False


def test_model_version_pin_sets_env(monkeypatch):
    monkeypatch.delenv(config.MODEL_VERSION_ENV, raising=False)
    db = FakeDBClient(rows=[])
    predict_fn = _make_predict_fn(["low"])

    run_batch(
        _args(model_version="3"),
        deps=_build_deps(db, predict_fn, model_version="3"),
    )

    assert os.environ.get(config.MODEL_VERSION_ENV) == "3"


def test_class_distribution_and_avg_confidence_in_summary():
    rows = [TicketRow(f"T-{i}", f"t{i}") for i in range(5)]
    db = FakeDBClient(rows=rows)
    # Priorities: [low, medium, medium, high, urgent]
    # Confidences: [0.5, 0.6, 0.7, 0.8, 0.9] -> avg = 0.7
    predict_fn = _make_predict_fn(
        ["low", "medium", "medium", "high", "urgent"],
        confidences=[0.5, 0.6, 0.7, 0.8, 0.9],
    )

    summary = run_batch(_args(), deps=_build_deps(db, predict_fn))

    assert summary["class_distribution"] == {"low": 1, "medium": 2, "high": 1, "urgent": 1}
    assert summary["avg_confidence"] == pytest.approx(0.7, rel=1e-6)


def test_avg_confidence_three_values():
    rows = [TicketRow(f"T-{i}", f"t{i}") for i in range(3)]
    db = FakeDBClient(rows=rows)
    predict_fn = _make_predict_fn(["low", "low", "low"], confidences=[0.5, 0.7, 0.9])

    summary = run_batch(_args(), deps=_build_deps(db, predict_fn))

    assert summary["avg_confidence"] == pytest.approx(0.7, rel=1e-6)


def test_default_selection_mode_and_fetch_params():
    db = FakeDBClient(rows=[])
    predict_fn = _make_predict_fn(["low"])

    summary = run_batch(
        _args(db_fetch_size=500),
        deps=_build_deps(db, predict_fn),
    )

    assert summary["selection_mode"] == "default"
    assert db.fetch_calls[0]["since"] is None
    assert db.fetch_calls[0]["ticket_ids"] is None
    assert db.fetch_calls[0]["batch_fetch_size"] == 500


def test_db_close_called_on_predict_exception():
    rows = [TicketRow("T-1", "a")]
    db = FakeDBClient(rows=rows)

    def bad_predict(texts, ids=None, *, batch_size=32):
        raise RuntimeError("boom")

    deps = BatchDeps(
        db_client_factory=lambda: db,
        load_model_fn=_stub_load_model(),
        predict_fn=bad_predict,
    )

    with pytest.raises(RuntimeError, match="boom"):
        run_batch(_args(), deps=deps)

    assert db.closed is True


def test_summary_run_id_uses_override():
    db = FakeDBClient(rows=[])
    predict_fn = _make_predict_fn(["low"])

    summary = run_batch(
        _args(run_id="my-custom-id"),
        deps=_build_deps(db, predict_fn),
    )

    assert summary["run_id"] == "my-custom-id"


def test_summary_run_id_auto_generated_when_none():
    db = FakeDBClient(rows=[])
    predict_fn = _make_predict_fn(["low"])

    summary = run_batch(
        _args(run_id=None),
        deps=_build_deps(db, predict_fn),
    )

    assert summary["run_id"].startswith("pred-")
