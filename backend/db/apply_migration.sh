#!/usr/bin/env bash
# Apply a single migration file from backend/db/migrations/ to the Cloud SQL
# Postgres instance using the Cloud SQL Auth Proxy.
#
# Usage:
#   bash backend/db/apply_migration.sh [path/to/migration.sql]
#
# If no path is given, defaults to backend/db/migrations/001_add_user_accounts.sql.
#
# Requirements on PATH:
#   - gcloud (authenticated)
#   - psql
#   - curl (to download cloud-sql-proxy if missing)
#
# Secrets are read from Secret Manager; nothing is printed.
set -euo pipefail

PROJECT_ID="msds-603-victors-demons"
REGION="us-central1"
INSTANCE_NAME="ticket-assistant-db"
INSTANCE_CONNECTION_NAME="${PROJECT_ID}:${REGION}:${INSTANCE_NAME}"
DB_NAME="ticket_assistant"
ROOT_SECRET="ticket-assistant-db-root-password"
PROXY_PORT="${PROXY_PORT:-5433}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_MIGRATION="${SCRIPT_DIR}/migrations/001_add_user_accounts.sql"
MIGRATION_FILE="${1:-$DEFAULT_MIGRATION}"

# Resolve relative paths against CWD; if not found, try relative to SCRIPT_DIR
if [[ ! -f "$MIGRATION_FILE" ]]; then
  if [[ -f "${SCRIPT_DIR}/${MIGRATION_FILE}" ]]; then
    MIGRATION_FILE="${SCRIPT_DIR}/${MIGRATION_FILE}"
  else
    echo "Migration file not found: $MIGRATION_FILE" >&2
    exit 1
  fi
fi

echo "Applying migration: $MIGRATION_FILE"

# --- Locate or download cloud-sql-proxy --------------------------------------
PROXY_BIN="$(command -v cloud-sql-proxy || true)"
if [[ -z "$PROXY_BIN" ]]; then
  TMP_PROXY_DIR="$(mktemp -d)"
  OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
  ARCH_RAW="$(uname -m)"
  case "$ARCH_RAW" in
    x86_64|amd64) ARCH="amd64" ;;
    arm64|aarch64) ARCH="arm64" ;;
    *) echo "Unsupported arch: $ARCH_RAW" >&2; exit 1 ;;
  esac
  PROXY_URL="https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.11.4/cloud-sql-proxy.${OS}.${ARCH}"
  echo "Downloading cloud-sql-proxy from ${PROXY_URL}"
  curl -fsSL -o "${TMP_PROXY_DIR}/cloud-sql-proxy" "$PROXY_URL"
  chmod +x "${TMP_PROXY_DIR}/cloud-sql-proxy"
  PROXY_BIN="${TMP_PROXY_DIR}/cloud-sql-proxy"
fi

# --- Fetch secrets -----------------------------------------------------------
ROOT_PW="$(gcloud secrets versions access latest --secret="$ROOT_SECRET" --project="$PROJECT_ID")"

# --- Start proxy in background ----------------------------------------------
PROXY_LOG="$(mktemp)"
"$PROXY_BIN" --port "$PROXY_PORT" "$INSTANCE_CONNECTION_NAME" >"$PROXY_LOG" 2>&1 &
PROXY_PID=$!

cleanup() {
  if kill -0 "$PROXY_PID" 2>/dev/null; then
    kill "$PROXY_PID" 2>/dev/null || true
    wait "$PROXY_PID" 2>/dev/null || true
  fi
  rm -f "$PROXY_LOG"
}
trap cleanup EXIT

# Wait for proxy to become ready
for _ in $(seq 1 30); do
  if (echo > "/dev/tcp/127.0.0.1/${PROXY_PORT}") 2>/dev/null; then
    break
  fi
  sleep 1
done

if ! (echo > "/dev/tcp/127.0.0.1/${PROXY_PORT}") 2>/dev/null; then
  echo "Cloud SQL proxy failed to open port ${PROXY_PORT}. Log:" >&2
  cat "$PROXY_LOG" >&2
  exit 1
fi

# --- Apply migration --------------------------------------------------------
PGPASSWORD="$ROOT_PW" psql \
  --host=127.0.0.1 --port="$PROXY_PORT" \
  --username=postgres --dbname="$DB_NAME" \
  --set=ON_ERROR_STOP=1 \
  -f "$MIGRATION_FILE"

# --- Verify -----------------------------------------------------------------
echo ""
echo "=== Verification ==="
PGPASSWORD="$ROOT_PW" psql \
  --host=127.0.0.1 --port="$PROXY_PORT" \
  --username=postgres --dbname="$DB_NAME" \
  --set=ON_ERROR_STOP=1 \
  -c "\d tickets" \
  -c "\d rate_limit_counters" \
  -c "SELECT 'tickets' AS table, COUNT(*) AS rows FROM tickets
      UNION ALL SELECT 'predictions',          COUNT(*) FROM predictions
      UNION ALL SELECT 'feedback',             COUNT(*) FROM feedback
      UNION ALL SELECT 'rate_limit_counters',  COUNT(*) FROM rate_limit_counters;"

echo "Migration applied successfully."
