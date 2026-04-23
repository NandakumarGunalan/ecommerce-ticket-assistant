"""Cloud SQL (Postgres) client for the backend API.

Exposes a small interface — :class:`DBClient` with three behaviors:

- ``insert_ticket_and_prediction`` — atomically create a ticket row and
  its initial prediction row (both in one transaction, so a prediction
  never references a ticket that doesn't exist).
- ``list_tickets`` — join each ticket with its most-recent prediction and
  return the records sorted by priority-rank then ``created_at DESC``.
- ``insert_feedback`` — record a thumbs up/down against a prediction.

Production path uses ``google.cloud.sql.connector.Connector`` with
``pg8000`` — matches the pattern already used by the batch inference
(``inference/db.py``) and avoids the need for a sidecar Cloud SQL Auth
Proxy inside the Cloud Run container.

Tests don't exercise this module; they inject an :class:`InMemoryDBClient`
(same public surface, dict-backed) so the schema-specific Postgres
features (UUID defaults, JSONB, ``ON DELETE CASCADE``) don't have to be
emulated in SQLite.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional, Protocol

from backend.api import config
from backend.api.logging_utils import get_logger

_LOG = get_logger(__name__)


# ---------------------------------------------------------------------------
# Row-ish DTO returned by both impls
# ---------------------------------------------------------------------------


@dataclass
class TicketPredictionRecord:
    """Flat DTO combining a ticket and its most-recent prediction.

    Mirrors ``backend.api.schemas.TicketRecord``; the API layer converts
    to the pydantic model for JSON serialization. Fields on the
    prediction side are ``Optional`` so a ticket without a prediction
    still has a representation (the API then fills in
    ``predicted_priority="unknown"``).
    """

    ticket_id: str
    created_at: datetime
    prediction_id: Optional[str]
    predicted_priority: Optional[str]
    confidence: Optional[float]
    all_scores: Optional[Dict[str, float]]
    model_version: Optional[str]
    model_run_id: Optional[str]
    latency_ms: Optional[int]


# ---------------------------------------------------------------------------
# Protocol / interface the API layer depends on
# ---------------------------------------------------------------------------


class DBClient(Protocol):
    def insert_ticket_and_prediction(
        self,
        *,
        ticket_text: str,
        predicted_priority: str,
        confidence: float,
        all_scores: Dict[str, float],
        model_version: str,
        model_run_id: Optional[str],
        latency_ms: int,
        source: str = "paste",
    ) -> TicketPredictionRecord: ...

    def list_tickets(self, limit: int = 50) -> List[TicketPredictionRecord]: ...

    def insert_feedback(
        self,
        *,
        prediction_id: str,
        verdict: str,
        note: Optional[str] = None,
    ) -> tuple[str, datetime]:
        """Return (feedback_id, created_at).

        Raises ``LookupError`` if ``prediction_id`` does not exist.
        """

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementation — used by tests, not production
# ---------------------------------------------------------------------------


class InMemoryDBClient:
    """Dict-backed stand-in for tests.

    Enforces the one FK constraint the API contract actually cares about
    (``feedback.prediction_id`` must reference an existing prediction);
    everything else is liberal because tests construct inputs directly.
    """

    def __init__(self) -> None:
        self._tickets: Dict[str, Dict[str, Any]] = {}
        self._predictions: Dict[str, Dict[str, Any]] = {}
        self._feedback: Dict[str, Dict[str, Any]] = {}
        self._lock = RLock()

    def insert_ticket_and_prediction(
        self,
        *,
        ticket_text: str,
        predicted_priority: str,
        confidence: float,
        all_scores: Dict[str, float],
        model_version: str,
        model_run_id: Optional[str],
        latency_ms: int,
        source: str = "paste",
    ) -> TicketPredictionRecord:
        with self._lock:
            ticket_id = str(uuid.uuid4())
            prediction_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            self._tickets[ticket_id] = {
                "id": ticket_id,
                "text": ticket_text,
                "source": source,
                "created_at": now,
            }
            self._predictions[prediction_id] = {
                "id": prediction_id,
                "ticket_id": ticket_id,
                "predicted_priority": predicted_priority,
                "confidence": confidence,
                "all_scores": dict(all_scores),
                "model_version": model_version,
                "model_run_id": model_run_id,
                "latency_ms": latency_ms,
                "created_at": now,
            }
            return TicketPredictionRecord(
                ticket_id=ticket_id,
                created_at=now,
                prediction_id=prediction_id,
                predicted_priority=predicted_priority,
                confidence=confidence,
                all_scores=dict(all_scores),
                model_version=model_version,
                model_run_id=model_run_id,
                latency_ms=latency_ms,
            )

    def list_tickets(self, limit: int = 50) -> List[TicketPredictionRecord]:
        with self._lock:
            # Group predictions by ticket and pick most-recent per ticket.
            latest_by_ticket: Dict[str, Dict[str, Any]] = {}
            for pred in self._predictions.values():
                tid = pred["ticket_id"]
                cur = latest_by_ticket.get(tid)
                if cur is None or pred["created_at"] > cur["created_at"]:
                    latest_by_ticket[tid] = pred

            records: List[TicketPredictionRecord] = []
            for ticket_id, ticket in self._tickets.items():
                pred = latest_by_ticket.get(ticket_id)
                records.append(
                    TicketPredictionRecord(
                        ticket_id=ticket_id,
                        created_at=ticket["created_at"],
                        prediction_id=pred["id"] if pred else None,
                        predicted_priority=pred["predicted_priority"] if pred else None,
                        confidence=pred["confidence"] if pred else None,
                        all_scores=dict(pred["all_scores"]) if pred else None,
                        model_version=pred["model_version"] if pred else None,
                        model_run_id=pred["model_run_id"] if pred else None,
                        latency_ms=pred["latency_ms"] if pred else None,
                    )
                )
            records.sort(
                key=lambda r: (
                    _priority_rank(r.predicted_priority),
                    -r.created_at.timestamp(),
                )
            )
            return records[:limit]

    def insert_feedback(
        self,
        *,
        prediction_id: str,
        verdict: str,
        note: Optional[str] = None,
    ) -> tuple[str, datetime]:
        with self._lock:
            if prediction_id not in self._predictions:
                raise LookupError(
                    f"prediction_id {prediction_id} does not exist"
                )
            feedback_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            self._feedback[feedback_id] = {
                "id": feedback_id,
                "prediction_id": prediction_id,
                "verdict": verdict,
                "note": note,
                "created_at": now,
            }
            return feedback_id, now

    def get_prediction_context(
        self, prediction_id: str
    ) -> Optional[Dict[str, Any]]:
        """Test convenience: return prediction + joined ticket id."""
        with self._lock:
            p = self._predictions.get(prediction_id)
            if p is None:
                return None
            return dict(p)

    def close(self) -> None:  # pragma: no cover — nothing to close
        return None


def _priority_rank(priority: Optional[str]) -> int:
    """Return an ordinal where lower = higher priority.

    Anything not in ``PRIORITY_ORDER`` (including ``None`` and
    ``"unknown"``) sorts after every known priority.
    """
    if priority is None:
        return len(config.PRIORITY_ORDER)
    try:
        return config.PRIORITY_ORDER.index(priority)
    except ValueError:
        return len(config.PRIORITY_ORDER)


# ---------------------------------------------------------------------------
# Postgres implementation — used in production on Cloud Run
# ---------------------------------------------------------------------------


class PostgresDBClient:
    """SQLAlchemy-backed client for the production Postgres instance.

    Connection strategy: ``cloud-sql-python-connector`` with ``pg8000``.
    No auth proxy sidecar needed — the connector handles IAM /
    password-based auth directly from the Cloud Run runtime.

    Password comes from Secret Manager at construction time. We do NOT
    cache or log the value anywhere; it lives in process memory on the
    SQLAlchemy connection factory closure only.
    """

    def __init__(
        self,
        *,
        instance_connection_name: str,
        db_name: str,
        db_user: str,
        db_password: str,
    ) -> None:
        # Imports inside __init__ so that tests, which only touch
        # InMemoryDBClient, don't need pg8000 / sqlalchemy / the connector
        # on their path. The production container always has them.
        import sqlalchemy  # type: ignore
        from google.cloud.sql.connector import Connector  # type: ignore

        self._connector = Connector()

        def _getconn():
            return self._connector.connect(
                instance_connection_name,
                "pg8000",
                user=db_user,
                password=db_password,
                db=db_name,
            )

        self._engine = sqlalchemy.create_engine(
            "postgresql+pg8000://",
            creator=_getconn,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
        )
        self._text = sqlalchemy.text

    # -- writes -------------------------------------------------------------

    def insert_ticket_and_prediction(
        self,
        *,
        ticket_text: str,
        predicted_priority: str,
        confidence: float,
        all_scores: Dict[str, float],
        model_version: str,
        model_run_id: Optional[str],
        latency_ms: int,
        source: str = "paste",
    ) -> TicketPredictionRecord:
        """Two INSERTs in one transaction.

        ``RETURNING id, created_at`` on each avoids a second round-trip to
        fetch the DB-generated UUIDs / timestamps. The JSONB column
        accepts a JSON string literal from pg8000 (the driver binds TEXT
        by default; Postgres casts implicitly).
        """
        insert_ticket_sql = self._text(
            """
            INSERT INTO tickets (text, source)
            VALUES (:text, :source)
            RETURNING id, created_at
            """
        )
        insert_pred_sql = self._text(
            """
            INSERT INTO predictions (
                ticket_id, predicted_priority, confidence, all_scores,
                model_version, model_run_id, latency_ms
            ) VALUES (
                :ticket_id, :predicted_priority, :confidence,
                CAST(:all_scores AS JSONB),
                :model_version, :model_run_id, :latency_ms
            )
            RETURNING id, created_at
            """
        )
        with self._engine.begin() as conn:
            t_row = conn.execute(
                insert_ticket_sql,
                {"text": ticket_text, "source": source},
            ).one()
            ticket_id = str(t_row[0])
            ticket_created_at = t_row[1]
            p_row = conn.execute(
                insert_pred_sql,
                {
                    "ticket_id": ticket_id,
                    "predicted_priority": predicted_priority,
                    "confidence": float(confidence),
                    "all_scores": json.dumps(all_scores),
                    "model_version": model_version,
                    "model_run_id": model_run_id,
                    "latency_ms": int(latency_ms),
                },
            ).one()
            prediction_id = str(p_row[0])

        return TicketPredictionRecord(
            ticket_id=ticket_id,
            created_at=ticket_created_at,
            prediction_id=prediction_id,
            predicted_priority=predicted_priority,
            confidence=confidence,
            all_scores=dict(all_scores),
            model_version=model_version,
            model_run_id=model_run_id,
            latency_ms=latency_ms,
        )

    def insert_feedback(
        self,
        *,
        prediction_id: str,
        verdict: str,
        note: Optional[str] = None,
    ) -> tuple[str, datetime]:
        # Existence check first so we can return a clean 404 at the API
        # layer. Doing this inside the same transaction avoids a TOCTOU
        # gap where the prediction is deleted between the check and the
        # insert.
        check_sql = self._text(
            "SELECT 1 FROM predictions WHERE id = CAST(:pid AS UUID)"
        )
        insert_sql = self._text(
            """
            INSERT INTO feedback (prediction_id, verdict, note)
            VALUES (CAST(:pid AS UUID), :verdict, :note)
            RETURNING id, created_at
            """
        )
        with self._engine.begin() as conn:
            exists = conn.execute(check_sql, {"pid": prediction_id}).first()
            if exists is None:
                raise LookupError(
                    f"prediction_id {prediction_id} does not exist"
                )
            row = conn.execute(
                insert_sql,
                {
                    "pid": prediction_id,
                    "verdict": verdict,
                    "note": note,
                },
            ).one()
            return str(row[0]), row[1]

    # -- reads --------------------------------------------------------------

    def list_tickets(self, limit: int = 50) -> List[TicketPredictionRecord]:
        """Join tickets to most-recent prediction.

        The DISTINCT ON pattern is Postgres-idiomatic: sort by
        ``(ticket_id, created_at DESC)`` and keep only the first row per
        ``ticket_id``. Ordering for the final response is applied in the
        outer query using a CASE expression that maps each priority
        string to its rank.
        """
        sql = self._text(
            """
            WITH latest_pred AS (
                SELECT DISTINCT ON (ticket_id)
                    ticket_id, id AS prediction_id,
                    predicted_priority, confidence, all_scores,
                    model_version, model_run_id, latency_ms, created_at
                FROM predictions
                ORDER BY ticket_id, created_at DESC
            )
            SELECT
                t.id AS ticket_id,
                t.created_at AS ticket_created_at,
                lp.prediction_id,
                lp.predicted_priority,
                lp.confidence,
                lp.all_scores,
                lp.model_version,
                lp.model_run_id,
                lp.latency_ms
            FROM tickets t
            LEFT JOIN latest_pred lp ON lp.ticket_id = t.id
            ORDER BY
                CASE lp.predicted_priority
                    WHEN 'urgent' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                    ELSE 4
                END,
                t.created_at DESC
            LIMIT :limit
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"limit": int(limit)}).all()

        records: List[TicketPredictionRecord] = []
        for r in rows:
            scores_raw = r[5]
            if isinstance(scores_raw, str):
                scores: Optional[Dict[str, float]] = json.loads(scores_raw)
            else:
                # pg8000 returns JSONB as dict already
                scores = scores_raw if scores_raw is not None else None
            records.append(
                TicketPredictionRecord(
                    ticket_id=str(r[0]),
                    created_at=r[1],
                    prediction_id=str(r[2]) if r[2] is not None else None,
                    predicted_priority=r[3],
                    confidence=float(r[4]) if r[4] is not None else None,
                    all_scores=scores,
                    model_version=r[6],
                    model_run_id=r[7],
                    latency_ms=int(r[8]) if r[8] is not None else None,
                )
            )
        return records

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        try:
            self._engine.dispose()
        finally:
            try:
                self._connector.close()
            except Exception:  # noqa: BLE001 — best-effort
                pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_postgres_client_from_env() -> PostgresDBClient:
    """Construct the production client from env + Secret Manager.

    Reads:
      - ``DB_INSTANCE`` — instance connection name
      - ``DB_NAME`` — database name
      - ``DB_USER`` — Postgres role
      - ``DB_PASSWORD_SECRET`` — name of the Secret Manager secret that
        holds the ``DB_USER`` password
      - ``GCP_PROJECT`` — project id that owns the secret
    """
    instance = config.require_env(config.DB_INSTANCE_ENV)
    db_name = config.require_env(config.DB_NAME_ENV)
    db_user = config.require_env(config.DB_USER_ENV)
    secret_name = config.require_env(config.DB_PASSWORD_SECRET_ENV)
    project = config.require_env(config.GCP_PROJECT_ENV)

    password = _fetch_secret(project=project, secret_name=secret_name)
    return PostgresDBClient(
        instance_connection_name=instance,
        db_name=db_name,
        db_user=db_user,
        db_password=password,
    )


def _fetch_secret(*, project: str, secret_name: str) -> str:
    """Return the latest version of ``secret_name`` as a string.

    Deliberately does not log the payload. If you need to debug the
    fetch, log length / version name — never the value.
    """
    # Import inside the function — Secret Manager client pulls in a lot
    # of transitive deps we don't want unit tests to require.
    from google.cloud import secretmanager  # type: ignore

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8")


# Keep os import referenced — several helpers access os.environ via
# config.require_env but some IDEs flag the import otherwise.
_ = os
