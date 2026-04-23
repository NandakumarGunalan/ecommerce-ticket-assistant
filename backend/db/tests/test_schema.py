"""Static sanity checks on backend/db/schema.sql.

Live-DB correctness is covered by backend/db/apply_schema.sh (which runs `\\dt`
and row-count queries after applying the schema). These tests only verify
that the DDL file parses and declares the tables, columns, and constraints
the backend depends on, so a bad edit to schema.sql fails CI without needing
a live Postgres connection.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlparse

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"


@pytest.fixture(scope="module")
def schema_sql() -> str:
    assert SCHEMA_PATH.is_file(), f"schema.sql missing at {SCHEMA_PATH}"
    return SCHEMA_PATH.read_text()


def test_schema_parses(schema_sql: str) -> None:
    """sqlparse can split the file into statements and none are empty noise."""
    statements = [
        s for s in sqlparse.split(schema_sql) if s.strip() and not s.strip().startswith("--")
    ]
    assert statements, "schema.sql parsed to zero statements"
    for stmt in statements:
        parsed = sqlparse.parse(stmt)
        assert parsed, f"sqlparse could not parse: {stmt[:80]!r}"


def test_pgcrypto_extension_declared(schema_sql: str) -> None:
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in schema_sql


@pytest.mark.parametrize("table", ["tickets", "predictions", "feedback"])
def test_tables_declared(schema_sql: str, table: str) -> None:
    assert f"CREATE TABLE IF NOT EXISTS {table}" in schema_sql


@pytest.mark.parametrize(
    "column",
    [
        # tickets
        "text TEXT NOT NULL",
        "source TEXT NOT NULL DEFAULT 'paste'",
        # predictions
        "ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE",
        "predicted_priority TEXT NOT NULL",
        "confidence DOUBLE PRECISION NOT NULL",
        "all_scores JSONB NOT NULL",
        "model_version TEXT NOT NULL",
        "model_run_id TEXT",
        "latency_ms INTEGER",
        # feedback
        "prediction_id UUID NOT NULL REFERENCES predictions(id) ON DELETE CASCADE",
        "verdict TEXT NOT NULL CHECK (verdict IN ('thumbs_up', 'thumbs_down'))",
    ],
)
def test_expected_column_declared(schema_sql: str, column: str) -> None:
    assert column in schema_sql, f"missing column declaration: {column}"


@pytest.mark.parametrize(
    "idx",
    [
        "idx_predictions_ticket_id",
        "idx_predictions_created_at",
        "idx_feedback_prediction_id",
    ],
)
def test_indexes_declared(schema_sql: str, idx: str) -> None:
    assert f"CREATE INDEX IF NOT EXISTS {idx}" in schema_sql


def test_app_user_grants(schema_sql: str) -> None:
    assert "GRANT USAGE ON SCHEMA public TO app_user" in schema_sql
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON tickets, predictions, feedback TO app_user"
        in schema_sql
    )
