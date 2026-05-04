"""Cloud SQL (Postgres) client for the batch inference pipeline.

Responsibilities:

- Read tickets that need scoring for a given model version (three selection
  modes: default unscored-only, ``--since`` date filter, explicit
  ``--ticket-ids`` list).
- Write ``PredictionRow`` rows into the ``predictions`` table.

All SQL is centralized here so that ``predictor.py`` and ``batch_predict.py``
don't carry DB knowledge. Production uses ``pg8000`` + Google's
``cloud-sql-python-connector`` for IAM-based auth — no password, no proxy
sidecar. Unit tests inject a SQLAlchemy engine pointed at an in-memory SQLite
database; the client detects the dialect and swaps a SQLite-compatible
INSERT template accordingly (testing-only accommodation — production is
strictly Postgres).

Schema notes (matches ``backend/db/schema.sql`` — the production source of
truth). The inference module historically targeted a different schema with
``ticket_id``/``ticket_text`` column names and a ``(ticket_id, model_version)``
unique constraint; we now alias those columns in SELECTs and accept that
re-running the batch can produce additional prediction rows for the same
ticket. The backend's ``list_tickets`` query already collapses to the most
recent prediction via ``DISTINCT ON``, so duplicates are harmless.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional

import sqlalchemy
from sqlalchemy import text
from sqlalchemy.engine import Engine

from inference import config
from inference.logging_utils import get_logger

_LOG = get_logger(__name__)


# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------


@dataclass
class TicketRow:
    """A ticket fetched from the source tickets table that needs scoring."""

    ticket_id: str
    ticket_text: str


@dataclass
class PredictionRow:
    """One row destined for the predictions table.

    ``created_at`` is populated by the DB default (``NOW()``) so it is not
    carried on this struct. ``batch_run_id`` is retained for log-trace
    purposes — it surfaces in batch summary log lines but is *not* written
    to the DB (no such column in the production schema). If we later add a
    ``batch_run_id`` column it will start being persisted automatically.
    """

    ticket_id: str
    model_version: str
    model_run_id: str
    predicted_priority: str
    confidence: float
    all_scores: Dict[str, float]  # e.g. {"low": 0.02, ...}
    batch_run_id: str


# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

# Default fetch: every ticket that has no row in predictions for this
# model_version. LEFT JOIN + IS NULL is the canonical "anti-join" shape and
# plays well with the existing index on predictions(ticket_id).
#
# Aliases (``t.id AS ticket_id``, ``t.text AS ticket_text``) bridge the
# production schema (which uses ``id`` / ``text``) to the names the rest of
# this module already uses. The backend code uses the same aliasing pattern
# in :mod:`backend.api.db_client`.
_FETCH_DEFAULT_SQL = """
SELECT t.id AS ticket_id, t.text AS ticket_text
FROM tickets t
LEFT JOIN predictions p
    ON p.ticket_id = t.id AND p.model_version = :model_version
WHERE p.ticket_id IS NULL
"""

# With `since`: same anti-join, additional created_at floor.
_FETCH_SINCE_SQL = """
SELECT t.id AS ticket_id, t.text AS ticket_text
FROM tickets t
LEFT JOIN predictions p
    ON p.ticket_id = t.id AND p.model_version = :model_version
WHERE p.ticket_id IS NULL AND t.created_at >= :since
"""

# Explicit id list: no anti-join, because the explicit-ids mode is the
# re-score escape hatch and must return rows even if they are already scored.
# Postgres ``ANY(:ids)`` accepts a Python list as an array parameter when
# used with pg8000; SQLite doesn't support the ANY-array form, so the
# client expands this to an IN (...) bind list at query-build time for
# SQLite only.
_FETCH_BY_IDS_SQL_PG = """
SELECT id AS ticket_id, text AS ticket_text
FROM tickets
WHERE id = ANY(CAST(:ticket_ids AS UUID[]))
"""

# Plain INSERT — no ON CONFLICT because the predictions table has no
# unique constraint on (ticket_id, model_version). Re-running the batch
# may write additional prediction rows for the same ticket; the backend's
# ``list_tickets`` query collapses to the most recent via ``DISTINCT ON``.
# RETURNING id lets us count how many rows actually hit disk.
#
# ``batch_run_id`` is intentionally NOT in the column list — no such column
# exists in production. It survives on :class:`PredictionRow` for log
# tracing only; if the schema later adds the column, persisting it is a
# one-line change here.
_INSERT_SQL_POSTGRES = """
INSERT INTO predictions (
    ticket_id, model_version, model_run_id, predicted_priority,
    confidence, all_scores
) VALUES (
    CAST(:ticket_id AS UUID), :model_version, :model_run_id,
    :predicted_priority, :confidence, CAST(:all_scores AS JSONB)
)
RETURNING id
"""

# In production there's no unique constraint to upsert against, so the
# "overwrite" path collapses to the same plain INSERT — the caller still
# gets an extra prediction row, which the backend's most-recent-wins read
# layer treats as the live value. Until the schema gains a unique index,
# overwrite=True and overwrite=False produce the same DB state.
_UPSERT_SQL_POSTGRES = _INSERT_SQL_POSTGRES

# SQLite variants for the test harness. The test schema (see
# inference/tests/test_db.py) mirrors the production columns: ``tickets(id,
# text)`` and ``predictions(id, ticket_id, ...)`` — no ``batch_run_id``,
# no unique constraint. Without JSONB, ``all_scores`` is stored as TEXT;
# the same JSON string payload works in both dialects without branching.
_INSERT_SQL_SQLITE = """
INSERT INTO predictions (
    ticket_id, model_version, model_run_id, predicted_priority,
    confidence, all_scores
) VALUES (
    :ticket_id, :model_version, :model_run_id,
    :predicted_priority, :confidence, :all_scores
)
RETURNING id
"""

_UPSERT_SQL_SQLITE = _INSERT_SQL_SQLITE


# ---------------------------------------------------------------------------
# Engine factory (production)
# ---------------------------------------------------------------------------


def _build_cloud_sql_engine() -> Engine:
    """Build a SQLAlchemy engine backed by the Cloud SQL Python Connector.

    Kept local to this function so that pulling in the connector (which
    transitively pulls in google-auth) only happens when we need the real
    engine. Tests that inject their own ``engine=`` never trigger this path.
    """
    # Imports inside the function: keep test runs (which pass engine=) from
    # paying the cost / needing the deps for connector + google-auth.
    from google.cloud.sql.connector import Connector  # type: ignore

    connection_name = os.environ[config.CLOUD_SQL_CONNECTION_NAME_ENV]
    db_name = os.environ[config.CLOUD_SQL_DB_NAME_ENV]
    db_user = os.environ[config.CLOUD_SQL_DB_USER_ENV]

    connector = Connector()

    def _getconn():
        return connector.connect(
            connection_name,
            "pg8000",
            user=db_user,
            db=db_name,
            enable_iam_auth=True,
        )

    engine = sqlalchemy.create_engine("postgresql+pg8000://", creator=_getconn)
    # Stash the connector on the engine so close() can dispose it cleanly;
    # otherwise the background keepalive threads leak on process exit.
    engine._inference_connector = connector  # type: ignore[attr-defined]
    return engine


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class DBClient:
    """Thin DB client over SQLAlchemy for inference I/O.

    Two construction modes:

    - Production: ``DBClient()`` — builds a Cloud SQL engine from env vars.
    - Tests: ``DBClient(engine=<sqlalchemy.Engine>)`` — caller supplies
      an engine (typically sqlite in-memory) with the expected schema
      already created.
    """

    def __init__(self, *, engine: Optional[Engine] = None) -> None:
        if engine is None:
            engine = _build_cloud_sql_engine()
        self._engine: Engine = engine
        self._dialect: str = engine.dialect.name  # "postgresql" or "sqlite"

    # -- reads --------------------------------------------------------------

    def fetch_unscored_tickets(
        self,
        model_version: str,
        *,
        since: Optional[str] = None,
        ticket_ids: Optional[List[str]] = None,
        batch_fetch_size: int = 1000,
    ) -> Iterator[TicketRow]:
        """Yield ``TicketRow`` objects matching the requested selection mode.

        Precedence (mutually exclusive in practice, matching the CLI spec):

        1. ``ticket_ids`` — fetch exactly these tickets, regardless of whether
           they are already scored. Used by ``--ticket-ids`` re-score mode.
        2. ``since`` — tickets with ``created_at >= since`` that are not yet
           scored for ``model_version``.
        3. Default — all tickets not yet scored for ``model_version``.

        Results are streamed in chunks of ``batch_fetch_size`` so that a
        large selection (backfill on a new model version, for example)
        does not OOM the container. This relies on SQLAlchemy's result
        ``yield_per`` to request server-side cursoring on Postgres; SQLite
        ignores the hint and simply buffers, which is fine for tests.
        """
        if ticket_ids is not None:
            stmt, params = self._build_fetch_by_ids(ticket_ids)
            mode = "ticket_ids"
        elif since is not None:
            stmt = text(_FETCH_SINCE_SQL)
            params = {"model_version": model_version, "since": since}
            mode = "since"
        else:
            stmt = text(_FETCH_DEFAULT_SQL)
            params = {"model_version": model_version}
            mode = "default"

        _LOG.info(
            "db.fetch_unscored_tickets",
            extra={
                "_json_payload": {
                    "event": "db.fetch_unscored_tickets",
                    "mode": mode,
                    "model_version": model_version,
                }
            },
        )

        # Use a plain connection (not a transaction) for the read path —
        # consistent snapshot isolation is not required here, and keeping
        # the txn open across a long streaming read would hold locks.
        with self._engine.connect() as conn:
            result = conn.execute(stmt, params)
            result = result.yield_per(batch_fetch_size)
            for row in result:
                # pg8000 returns UUID columns as uuid.UUID; downstream
                # consumers (pydantic Prediction model, log payloads) expect
                # plain strings. Coerce here so the rest of the pipeline
                # never sees a UUID instance.
                yield TicketRow(ticket_id=str(row[0]), ticket_text=row[1])

    def _build_fetch_by_ids(self, ticket_ids: List[str]):
        """Return (stmt, params) for the explicit-id fetch path.

        Postgres: use ``WHERE ticket_id = ANY(:ticket_ids)`` so a single
        bind carries the whole list — scales to thousands of ids without
        bloating the query text.

        SQLite: expand to ``WHERE ticket_id IN (:id0, :id1, ...)`` because
        SQLite has no array parameter type. An empty list short-circuits
        to ``WHERE 1=0`` to avoid generating invalid ``IN ()`` SQL.
        """
        if self._dialect == "postgresql":
            return text(_FETCH_BY_IDS_SQL_PG), {"ticket_ids": list(ticket_ids)}
        # sqlite fallback
        if not ticket_ids:
            return text("SELECT id AS ticket_id, text AS ticket_text FROM tickets WHERE 1=0"), {}
        placeholders = ", ".join(f":id{i}" for i in range(len(ticket_ids)))
        stmt = text(
            f"SELECT id AS ticket_id, text AS ticket_text FROM tickets "
            f"WHERE id IN ({placeholders})"
        )
        params = {f"id{i}": tid for i, tid in enumerate(ticket_ids)}
        return stmt, params

    # -- writes -------------------------------------------------------------

    def insert_predictions(
        self,
        predictions: Iterable[PredictionRow],
        *,
        overwrite: bool = False,
    ) -> int:
        """Insert (or upsert) a batch of predictions in a single transaction.

        Returns the number of rows the DB actually wrote — i.e. the length
        of ``RETURNING ticket_id``. In the default (``overwrite=False``)
        path this excludes rows that hit the ``ON CONFLICT DO NOTHING``
        branch, so the caller can tell "N predictions submitted, K actually
        landed" and log the skipped count.

        ``all_scores`` is serialized to JSON text here. Postgres' JSONB
        column happily accepts a JSON string literal; the SQLite test
        schema uses TEXT for the same column, so the same payload works in
        both dialects without branching.
        """
        rows_list: List[PredictionRow] = list(predictions)
        if not rows_list:
            return 0

        if overwrite:
            sql = _UPSERT_SQL_POSTGRES if self._dialect == "postgresql" else _UPSERT_SQL_SQLITE
        else:
            sql = _INSERT_SQL_POSTGRES if self._dialect == "postgresql" else _INSERT_SQL_SQLITE
        stmt = text(sql)

        written = 0
        with self._engine.begin() as conn:
            # One round-trip per row so that RETURNING semantics line up
            # across dialects (SQLAlchemy's executemany does not reliably
            # surface RETURNING rows for pg8000). At our batch sizes
            # (≤ 1000 rows per call) this is fine; if it becomes a
            # bottleneck we can switch to a single INSERT with a VALUES
            # tuple list and still get RETURNING.
            for row in rows_list:
                # batch_run_id is intentionally absent — see SQL templates above.
                params: Dict[str, Any] = {
                    "ticket_id": row.ticket_id,
                    "model_version": row.model_version,
                    "model_run_id": row.model_run_id,
                    "predicted_priority": row.predicted_priority,
                    "confidence": float(row.confidence),
                    "all_scores": json.dumps(row.all_scores),
                }
                result = conn.execute(stmt, params)
                # RETURNING may yield zero rows (skip-on-conflict) or one row.
                returned = result.fetchall()
                written += len(returned)

        _LOG.info(
            "db.insert_predictions",
            extra={
                "_json_payload": {
                    "event": "db.insert_predictions",
                    "submitted": len(rows_list),
                    "written": written,
                    "overwrite": overwrite,
                }
            },
        )
        return written

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Dispose the engine and its underlying connector (if any).

        Safe to call multiple times. The Cloud SQL connector owns
        background threads for keepalives and token refresh; failing to
        close it leaves those threads alive until interpreter exit, which
        shows up as a warning on short-lived Cloud Run Job invocations.
        """
        connector = getattr(self._engine, "_inference_connector", None)
        try:
            self._engine.dispose()
        finally:
            if connector is not None:
                # Connector.close is idempotent.
                try:
                    connector.close()
                except Exception:  # noqa: BLE001 — best-effort cleanup
                    pass
