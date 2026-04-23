BEGIN;

-- Add a nullable resolved_at timestamp. NULL means the ticket is still open.
-- A populated value means the ticket has been marked resolved by its owner.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ NULL;

-- Compound index supports the two common read patterns:
--   WHERE user_id = $1 AND resolved_at IS NULL          (default list)
--   WHERE user_id = $1                                  (include_resolved=true)
CREATE INDEX IF NOT EXISTS idx_tickets_user_id_resolved_at
    ON tickets(user_id, resolved_at);

COMMIT;
