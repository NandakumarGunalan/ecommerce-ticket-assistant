# Backend database (Cloud SQL Postgres)

This directory holds the schema and helper script for the application database
that powers the FastAPI backend. The DB stores raw tickets, model predictions,
and user feedback.

## Instance

| Field                      | Value                                                      |
| -------------------------- | ---------------------------------------------------------- |
| GCP project                | `msds-603-victors-demons`                                  |
| Region                     | `us-central1`                                              |
| Instance name              | `ticket-assistant-db`                                      |
| Instance connection name   | `msds-603-victors-demons:us-central1:ticket-assistant-db`  |
| Engine                     | Postgres 15                                                |
| Tier                       | `db-f1-micro` (zonal, 10 GB SSD — class demo, ~$9/mo)      |
| Database                   | `ticket_assistant`                                         |
| Admin user                 | `postgres`                                                 |
| App user                   | `app_user`                                                 |

Passwords are **not** stored in this repo. They live in Secret Manager:

| Secret name                              | Purpose                                  |
| ---------------------------------------- | ---------------------------------------- |
| `ticket-assistant-db-root-password`      | Password for the `postgres` admin role   |
| `ticket-assistant-db-app-password`       | Password for the `app_user` role         |

The runtime service account
`inference-runner@msds-603-victors-demons.iam.gserviceaccount.com` has been
granted `roles/secretmanager.secretAccessor` on both secrets and
`roles/cloudsql.client` project-wide, so the FastAPI backend (deployed on
Cloud Run) can fetch the app password and connect via the Cloud SQL connector.

## Tables

All tables live in the `public` schema. UUID primary keys use `pgcrypto`'s
`gen_random_uuid()`.

### `tickets`
Raw ticket text received from the frontend.
- `id UUID PRIMARY KEY`
- `text TEXT NOT NULL` — ticket body
- `source TEXT NOT NULL DEFAULT 'paste'` — `paste` | `csv` | `api`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`

### `predictions`
One row per (ticket, model-run). A ticket may be scored multiple times.
- `id UUID PRIMARY KEY`
- `ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE`
- `predicted_priority TEXT NOT NULL` — e.g. `low`, `medium`, `high`
- `confidence DOUBLE PRECISION NOT NULL` — top-class probability
- `all_scores JSONB NOT NULL` — full class-probability map
- `model_version TEXT NOT NULL` — e.g. `distilbert-priority@v3`
- `model_run_id TEXT` — MLflow run id (optional)
- `latency_ms INTEGER` — inference latency
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- Indexes: `ticket_id`, `created_at DESC`

### `feedback`
Thumbs up / thumbs down per prediction from the human reviewer.
- `id UUID PRIMARY KEY`
- `prediction_id UUID NOT NULL REFERENCES predictions(id) ON DELETE CASCADE`
- `verdict TEXT NOT NULL CHECK (verdict IN ('thumbs_up','thumbs_down'))`
- `note TEXT`
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- Index: `prediction_id`

## Fetching passwords from Secret Manager

```bash
# Admin password (rarely needed; only for schema changes)
gcloud secrets versions access latest \
  --secret=ticket-assistant-db-root-password \
  --project=msds-603-victors-demons

# App password (used by the backend at runtime)
gcloud secrets versions access latest \
  --secret=ticket-assistant-db-app-password \
  --project=msds-603-victors-demons
```

Do **not** echo these to shared terminals, logs, or commit messages.

## Connecting locally via Cloud SQL Auth Proxy

1. Install the proxy if you don't have it:
   <https://cloud.google.com/sql/docs/postgres/sql-proxy>
2. Start it:
   ```bash
   cloud-sql-proxy --port 5433 \
     msds-603-victors-demons:us-central1:ticket-assistant-db
   ```
3. In another shell, connect with `psql`:
   ```bash
   PGPASSWORD="$(gcloud secrets versions access latest \
     --secret=ticket-assistant-db-app-password \
     --project=msds-603-victors-demons)" \
   psql --host=127.0.0.1 --port=5433 \
        --username=app_user --dbname=ticket_assistant
   ```

## Re-applying the schema

The schema is idempotent (every `CREATE` / `GRANT` uses `IF NOT EXISTS` or is
safe to rerun). To re-apply after editing `schema.sql`:

```bash
./backend/db/apply_schema.sh
```

The script:
- Downloads `cloud-sql-proxy` to a temp dir if not on `PATH`
- Starts the proxy in the background against the instance
- Fetches both passwords from Secret Manager
- Ensures `app_user` exists and its password matches the secret
- Runs `schema.sql` as `postgres`
- Prints `\dt` and row counts for verification
- Kills the proxy on exit (trap)

## Provisioning commands (for reference)

Run by the `feature/cloud-sql-schema` unit on 2026-04-23:

```bash
gcloud services enable secretmanager.googleapis.com --project=msds-603-victors-demons

# Secrets (passwords generated locally, never logged)
gcloud secrets create ticket-assistant-db-root-password \
  --project=msds-603-victors-demons --replication-policy=automatic \
  --data-file=<(printf '%s' "$ROOT_PW")
gcloud secrets create ticket-assistant-db-app-password  \
  --project=msds-603-victors-demons --replication-policy=automatic \
  --data-file=<(printf '%s' "$APP_PW")

# IAM
for s in ticket-assistant-db-root-password ticket-assistant-db-app-password; do
  gcloud secrets add-iam-policy-binding "$s" \
    --project=msds-603-victors-demons \
    --member="serviceAccount:inference-runner@msds-603-victors-demons.iam.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
gcloud projects add-iam-policy-binding msds-603-victors-demons \
  --member="serviceAccount:inference-runner@msds-603-victors-demons.iam.gserviceaccount.com" \
  --role=roles/cloudsql.client

# Instance
gcloud sql instances create ticket-assistant-db \
  --project=msds-603-victors-demons \
  --database-version=POSTGRES_15 --tier=db-f1-micro \
  --region=us-central1 --storage-size=10 --storage-type=SSD \
  --availability-type=zonal --root-password="$ROOT_PW"

gcloud sql databases create ticket_assistant \
  --instance=ticket-assistant-db --project=msds-603-victors-demons
gcloud sql users create app_user \
  --instance=ticket-assistant-db --project=msds-603-victors-demons \
  --password="$APP_PW"

./backend/db/apply_schema.sh
```
