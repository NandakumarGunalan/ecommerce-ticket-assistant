-- Schema for the ecommerce ticket assistant backend.
-- Applied against Cloud SQL Postgres 15 instance: ticket-assistant-db
-- Database: ticket_assistant
--
-- Safe to re-run: all statements are idempotent (IF NOT EXISTS / IF EXISTS).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- tickets: raw input from the frontend
CREATE TABLE IF NOT EXISTS tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'paste',  -- 'paste' | 'csv' | 'api'
    user_id TEXT NOT NULL,                 -- Firebase UID of the submitter
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tickets_user_id ON tickets(user_id);

-- predictions: one per ticket per model run (a ticket can be re-scored)
CREATE TABLE IF NOT EXISTS predictions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    predicted_priority TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    all_scores JSONB NOT NULL,
    model_version TEXT NOT NULL,
    model_run_id TEXT,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_predictions_ticket_id ON predictions(ticket_id);
CREATE INDEX IF NOT EXISTS idx_predictions_created_at ON predictions(created_at DESC);

-- feedback: thumbs up/down per prediction
CREATE TABLE IF NOT EXISTS feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prediction_id UUID NOT NULL REFERENCES predictions(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL CHECK (verdict IN ('thumbs_up', 'thumbs_down')),
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_feedback_prediction_id ON feedback(prediction_id);

-- rate_limit_counters: per-user per-minute request counters for API rate limiting
CREATE TABLE IF NOT EXISTS rate_limit_counters (
    user_id TEXT NOT NULL,
    window_start_minute TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, window_start_minute)
);
CREATE INDEX IF NOT EXISTS idx_rate_limit_counters_window ON rate_limit_counters(window_start_minute);

-- Grant app user CRUD on these tables
GRANT USAGE ON SCHEMA public TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON tickets, predictions, feedback TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON rate_limit_counters TO app_user;
