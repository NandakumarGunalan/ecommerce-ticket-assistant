BEGIN;

-- Wipe existing throwaway demo data (user-approved)
TRUNCATE TABLE tickets, predictions, feedback RESTART IDENTITY CASCADE;

-- Scope tickets by user
ALTER TABLE tickets ADD COLUMN user_id TEXT NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tickets_user_id ON tickets(user_id);

-- Rate limit counters
CREATE TABLE IF NOT EXISTS rate_limit_counters (
    user_id TEXT NOT NULL,
    window_start_minute TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, window_start_minute)
);
CREATE INDEX IF NOT EXISTS idx_rate_limit_counters_window ON rate_limit_counters(window_start_minute);

-- App user grants on the new table
GRANT SELECT, INSERT, UPDATE, DELETE ON rate_limit_counters TO app_user;

COMMIT;
