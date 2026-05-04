"""Unit tests for ``inference.db.DBClient``.

The production target is Cloud SQL (Postgres). Provisioning a real Postgres
for unit tests is overkill, so these tests run against an in-memory SQLite
database that mirrors the production schema (``backend/db/schema.sql``)
with TEXT in place of JSONB / UUID / TIMESTAMPTZ.

What these tests deliberately do NOT cover:

- JSONB storage / query semantics (SQLite treats the column as TEXT).
- ``NOW()`` / ``TIMESTAMPTZ`` default expression (we rely on the column
  definition in the migration, not anything DBClient does).
- ``cloud-sql-python-connector`` wiring — that path is short, untested
  here, and exercised end-to-end via the deployed Cloud Run Job.

These gaps are accepted: the SQL that runs on Postgres-in-production vs.
SQLite-in-tests is textually identical except for the handful of
dialect-branching accommodations documented in ``inference/db.py``.
"""
from __future__ import annotations

import json
from typing import List

import pytest
import sqlalchemy
from sqlalchemy import text

from inference.db import DBClient, PredictionRow, TicketRow


# ---------------------------------------------------------------------------
# Schema / fixtures
# ---------------------------------------------------------------------------

# Mirrors backend/db/schema.sql with SQLite-friendly types
# (TEXT for UUID/TIMESTAMPTZ, TEXT for JSONB). No unique constraint on
# (ticket_id, model_version) — matches production. Multiple predictions
# for the same ticket are allowed; the backend collapses to "most recent
# wins" at read time.
_TICKETS_DDL = """
CREATE TABLE tickets (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'paste',
    user_id TEXT NOT NULL DEFAULT 'test-user',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT NULL
)
"""

_PREDICTIONS_DDL = """
CREATE TABLE predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    predicted_priority TEXT NOT NULL,
    confidence REAL NOT NULL,
    all_scores TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_run_id TEXT NOT NULL,
    latency_ms INTEGER NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@pytest.fixture()
def engine():
    """An in-memory SQLite engine with the expected test schema created."""
    eng = sqlalchemy.create_engine("sqlite:///:memory:", future=True)
    with eng.begin() as conn:
        conn.execute(text(_TICKETS_DDL))
        conn.execute(text(_PREDICTIONS_DDL))
    yield eng
    eng.dispose()


@pytest.fixture()
def seeded_engine(engine):
    """Engine pre-populated with four tickets having explicit ``created_at``
    values so that ``--since`` filtering is deterministic across runs."""
    rows = [
        ("T-001", "order never arrived", "2026-04-10 00:00:00"),
        ("T-002", "thanks for shipping",  "2026-04-15 00:00:00"),
        ("T-003", "return not processed", "2026-04-19 00:00:00"),
        ("T-004", "wrong item",           "2026-04-20 00:00:00"),
    ]
    with engine.begin() as conn:
        for tid, ttext, cts in rows:
            conn.execute(
                text(
                    "INSERT INTO tickets (id, text, created_at) "
                    "VALUES (:i, :t, :c)"
                ),
                {"i": tid, "t": ttext, "c": cts},
            )
    return engine


@pytest.fixture()
def client(seeded_engine):
    c = DBClient(engine=seeded_engine)
    yield c
    c.close()


def _pred(
    ticket_id: str,
    *,
    model_version: str = "1",
    predicted_priority: str = "medium",
    confidence: float = 0.77,
    batch_run_id: str = "run-test",
) -> PredictionRow:
    """Build a valid PredictionRow with reasonable defaults for terse tests."""
    return PredictionRow(
        ticket_id=ticket_id,
        model_version=model_version,
        model_run_id="run-20260419-140149",
        predicted_priority=predicted_priority,
        confidence=confidence,
        all_scores={"low": 0.05, "medium": 0.77, "high": 0.15, "urgent": 0.03},
        batch_run_id=batch_run_id,
    )


def _ids(tickets: List[TicketRow]) -> List[str]:
    return [t.ticket_id for t in tickets]


# ---------------------------------------------------------------------------
# Tests — required cases from the Phase 2 Wave 1 spec
# ---------------------------------------------------------------------------


def test_default_fetch_excludes_already_scored_for_same_version(client):
    """Default selection must anti-join on (ticket_id, model_version)."""
    # Pre-score T-001 and T-003 under model_version="1".
    client.insert_predictions([_pred("T-001"), _pred("T-003")])

    fetched = list(client.fetch_unscored_tickets("1"))
    # Only the unscored ones should come back.
    assert sorted(_ids(fetched)) == ["T-002", "T-004"]


def test_default_fetch_includes_tickets_scored_under_different_model_version(client):
    """A new model version must re-score every ticket — the anti-join is
    version-scoped, not blanket. This is what makes the "bump the default
    model and run the batch to backfill" flow work without extra flags."""
    # T-001 scored under v1; v2 should still see T-001 as unscored.
    client.insert_predictions([_pred("T-001", model_version="1")])

    fetched = list(client.fetch_unscored_tickets("2"))
    assert sorted(_ids(fetched)) == ["T-001", "T-002", "T-003", "T-004"]


def test_since_filter_restricts_by_created_at(client):
    """``--since`` is a lower bound on ``created_at`` and composes with the
    default anti-join (unscored tickets only)."""
    # No predictions yet — everything is unscored. Filter by date.
    fetched = list(client.fetch_unscored_tickets("1", since="2026-04-18"))
    # T-003 (2026-04-19) and T-004 (2026-04-20) pass the floor; T-001/T-002 don't.
    assert sorted(_ids(fetched)) == ["T-003", "T-004"]


def test_since_filter_still_excludes_already_scored(client):
    """``--since`` + anti-join: if a within-window ticket is already scored,
    it should NOT come back. This protects daily cron runs from re-scoring
    things that were manually backfilled earlier in the day."""
    client.insert_predictions([_pred("T-003")])
    fetched = list(client.fetch_unscored_tickets("1", since="2026-04-18"))
    assert _ids(fetched) == ["T-004"]


def test_ticket_ids_mode_fetches_exact_set_even_if_scored(client):
    """``--ticket-ids`` is the backfill escape hatch — it must return rows
    the caller asked for, even if they already have predictions."""
    # T-002 and T-004 already have predictions under v1.
    client.insert_predictions([_pred("T-002"), _pred("T-004")])

    fetched = list(client.fetch_unscored_tickets("1", ticket_ids=["T-002", "T-004"]))
    assert sorted(_ids(fetched)) == ["T-002", "T-004"]


def test_insert_appends_when_same_ticket_scored_again(client):
    """The production ``predictions`` schema has no unique constraint on
    ``(ticket_id, model_version)``, so a second insert *adds* a row rather
    than skipping. The backend's ``list_tickets`` collapses duplicates to
    the most recent prediction at read time."""
    first = client.insert_predictions([_pred("T-001", confidence=0.50)])
    assert first == 1

    again = client.insert_predictions([_pred("T-001", confidence=0.99)])
    assert again == 1

    with client._engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT confidence FROM predictions WHERE ticket_id='T-001' "
                "ORDER BY id"
            )
        ).all()
    assert [r[0] for r in rows] == [pytest.approx(0.50), pytest.approx(0.99)]


def test_insert_overwrite_true_currently_collapses_to_plain_insert(client):
    """``overwrite=True`` is the explicit re-score escape hatch. Until the
    schema gains a unique index on ``(ticket_id, model_version)`` it produces
    the same effect as a plain insert: an additional prediction row that
    becomes the most recent one. Documenting current behaviour so a future
    schema migration that re-enables true upsert can flip this assertion."""
    client.insert_predictions([_pred("T-001", confidence=0.50)])
    updated = client.insert_predictions(
        [_pred("T-001", confidence=0.99, predicted_priority="urgent")],
        overwrite=True,
    )
    assert updated == 1

    with client._engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT predicted_priority, confidence FROM predictions "
                "WHERE ticket_id='T-001' ORDER BY id"
            )
        ).all()
    assert len(rows) == 2
    # The newest row reflects the re-score input.
    assert rows[-1][0] == "urgent"
    assert rows[-1][1] == pytest.approx(0.99)


def test_insert_batch_writes_all_fresh_rows(client):
    """A batch of N distinct (ticket_id, model_version) pairs should all land."""
    rows = [_pred("T-001"), _pred("T-002"), _pred("T-003"), _pred("T-004")]
    written = client.insert_predictions(rows)
    assert written == 4

    with client._engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM predictions")).scalar()
    assert count == 4


def test_all_scores_roundtrips_as_json(client):
    """The ``all_scores`` dict must be stored verbatim so consumers can
    re-derive the full softmax distribution (needed for threshold tuning)."""
    scores = {"low": 0.01, "medium": 0.04, "high": 0.15, "urgent": 0.80}
    row = PredictionRow(
        ticket_id="T-001",
        model_version="1",
        model_run_id="run-x",
        predicted_priority="urgent",
        confidence=0.80,
        all_scores=scores,
        batch_run_id="run-test",
    )
    client.insert_predictions([row])

    with client._engine.connect() as conn:
        raw = conn.execute(
            text("SELECT all_scores FROM predictions WHERE ticket_id='T-001'")
        ).scalar()
    assert json.loads(raw) == scores


def test_empty_insert_is_a_noop(client):
    """Empty iterables must not touch the DB and must return 0 — avoids a
    "nothing to score" batch logging a bogus transaction."""
    assert client.insert_predictions([]) == 0


def test_empty_ticket_ids_list_returns_no_rows(client):
    """Edge case: explicit empty id list must not degenerate into
    'select every ticket' (which would be the worst possible footgun)."""
    fetched = list(client.fetch_unscored_tickets("1", ticket_ids=[]))
    assert fetched == []
