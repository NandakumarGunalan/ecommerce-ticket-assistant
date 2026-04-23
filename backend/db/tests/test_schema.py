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
MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "migrations" / "001_add_user_accounts.sql"
)


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


@pytest.mark.parametrize(
    "table", ["tickets", "predictions", "feedback", "rate_limit_counters"]
)
def test_tables_declared(schema_sql: str, table: str) -> None:
    assert f"CREATE TABLE IF NOT EXISTS {table}" in schema_sql


@pytest.mark.parametrize(
    "column",
    [
        # tickets
        "text TEXT NOT NULL",
        "source TEXT NOT NULL DEFAULT 'paste'",
        "user_id TEXT NOT NULL",
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
        "idx_tickets_user_id",
        "idx_rate_limit_counters_window",
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
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON rate_limit_counters TO app_user"
        in schema_sql
    )


def test_rate_limit_counters_shape(schema_sql: str) -> None:
    """rate_limit_counters must declare its columns and composite PK."""
    assert "user_id TEXT NOT NULL" in schema_sql
    assert "window_start_minute TIMESTAMPTZ NOT NULL" in schema_sql
    assert "count INTEGER NOT NULL DEFAULT 0" in schema_sql
    assert "PRIMARY KEY (user_id, window_start_minute)" in schema_sql


# --- Migration file checks --------------------------------------------------


@pytest.fixture(scope="module")
def migration_sql() -> str:
    assert MIGRATION_PATH.is_file(), f"migration missing at {MIGRATION_PATH}"
    return MIGRATION_PATH.read_text()


def test_migration_parses(migration_sql: str) -> None:
    """sqlparse can split the migration file into statements."""
    statements = [
        s for s in sqlparse.split(migration_sql) if s.strip() and not s.strip().startswith("--")
    ]
    assert statements, "migration parsed to zero statements"
    for stmt in statements:
        parsed = sqlparse.parse(stmt)
        assert parsed, f"sqlparse could not parse: {stmt[:80]!r}"


def test_migration_contents(migration_sql: str) -> None:
    """Migration 001 must wipe demo data, add user_id, and create rate_limit_counters."""
    assert "TRUNCATE TABLE tickets, predictions, feedback" in migration_sql
    assert "ALTER TABLE tickets ADD COLUMN user_id TEXT NOT NULL" in migration_sql
    assert "CREATE INDEX IF NOT EXISTS idx_tickets_user_id ON tickets(user_id)" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS rate_limit_counters" in migration_sql
    assert "PRIMARY KEY (user_id, window_start_minute)" in migration_sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON rate_limit_counters TO app_user" in migration_sql
    assert migration_sql.strip().startswith("BEGIN")
    assert "COMMIT" in migration_sql
