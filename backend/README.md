# Backend API — `ticket-backend-api`

FastAPI service deployed to Cloud Run. Public (no-auth) frontend-facing
HTTP layer that:

1. Proxies the IAM-restricted model endpoint
   (`distilbert-priority-online`) — attaches a Google-signed ID token on
   every request.
2. Persists tickets / predictions / feedback to Cloud SQL Postgres
   (instance `ticket-assistant-db`, db `ticket_assistant`).
3. Emits structured JSON logs (`event=ticket_created`, `event=feedback_recorded`,
   `event=model_endpoint_error`) that Cloud Run surfaces as `jsonPayload`.

## Endpoints

| Method | Path                | Shape                                                       |
| ------ | ------------------- | ----------------------------------------------------------- |
| GET    | `/healthz`          | Passthrough of the model endpoint's `/healthz`              |
| POST   | `/predict`          | Stateless score — no DB write                               |
| POST   | `/tickets`          | Score + INSERT ticket + INSERT prediction                   |
| GET    | `/tickets?limit=50` | Latest prediction per ticket, sorted by priority rank       |
| POST   | `/feedback`         | Thumbs up/down against a `prediction_id`                    |

Validation: `ticket_text` is required, non-empty, ≤ 10,000 chars;
`verdict` must be `thumbs_up` or `thumbs_down`.

## Environment variables

| Name                  | Purpose                                                                 |
| --------------------- | ----------------------------------------------------------------------- |
| `MODEL_ENDPOINT_URL`  | Full https URL of `distilbert-priority-online`                          |
| `DB_INSTANCE`         | Cloud SQL instance connection name `project:region:instance`            |
| `DB_NAME`             | Database name (`ticket_assistant`)                                      |
| `DB_USER`             | Postgres role (`app_user`)                                              |
| `DB_PASSWORD_SECRET`  | Secret Manager secret name (`ticket-assistant-db-app-password`)         |
| `GCP_PROJECT`         | Project id that owns the secret                                         |

## Local development

Tests mock the model endpoint and use an in-memory DB — they do **not**
require a live Postgres:

```bash
.venv/bin/python -m pytest backend/tests -q
```

To run the app locally against the real Cloud SQL instance, use the
Cloud SQL Auth Proxy (same pattern as `backend/db/apply_schema.sh`).

1. Start the proxy:
   ```bash
   cloud-sql-proxy --port 5433 \
     msds-603-victors-demons:us-central1:ticket-assistant-db
   ```
2. The production `PostgresDBClient` talks to the instance directly via
   the Cloud SQL Python Connector, not the proxy. For local uvicorn you
   have two options:
   - **Easy path:** run the app without the DB by stubbing out the
     `get_db` dependency — useful for iterating on routes.
   - **Full path:** fetch the app password and point the connector at
     the instance via Application Default Credentials:
     ```bash
     gcloud auth application-default login
     export MODEL_ENDPOINT_URL="$(gcloud run services describe distilbert-priority-online \
       --region=us-central1 --project=msds-603-victors-demons \
       --format='value(status.url)')"
     export DB_INSTANCE="msds-603-victors-demons:us-central1:ticket-assistant-db"
     export DB_NAME="ticket_assistant"
     export DB_USER="app_user"
     export DB_PASSWORD_SECRET="ticket-assistant-db-app-password"
     export GCP_PROJECT="msds-603-victors-demons"
     .venv/bin/uvicorn backend.api.main:app --reload --port 8080
     ```

## Deploy

Build:
```bash
gcloud builds submit --config=backend/cloudbuild.yaml \
  --project=msds-603-victors-demons .
```

Deploy:
```bash
MODEL_URL=$(gcloud run services describe distilbert-priority-online \
  --region=us-central1 --project=msds-603-victors-demons \
  --format='value(status.url)')

gcloud run deploy ticket-backend-api \
  --image=us-central1-docker.pkg.dev/msds-603-victors-demons/ml-repo/ticket-backend-api:latest \
  --region=us-central1 \
  --service-account=inference-runner@msds-603-victors-demons.iam.gserviceaccount.com \
  --allow-unauthenticated \
  --set-env-vars="MODEL_ENDPOINT_URL=$MODEL_URL,DB_INSTANCE=msds-603-victors-demons:us-central1:ticket-assistant-db,DB_NAME=ticket_assistant,DB_USER=app_user,DB_PASSWORD_SECRET=ticket-assistant-db-app-password,GCP_PROJECT=msds-603-victors-demons" \
  --add-cloudsql-instances=msds-603-victors-demons:us-central1:ticket-assistant-db \
  --cpu=1 --memory=512Mi \
  --min-instances=0 --max-instances=5 \
  --concurrency=20 --timeout=30s \
  --project=msds-603-victors-demons
```

Grant the runtime SA `run.invoker` on the model endpoint so it can
fetch ID tokens with the right audience:
```bash
gcloud run services add-iam-policy-binding distilbert-priority-online \
  --region=us-central1 \
  --member=serviceAccount:inference-runner@msds-603-victors-demons.iam.gserviceaccount.com \
  --role=roles/run.invoker \
  --project=msds-603-victors-demons
```

## Smoke tests

```bash
URL=$(gcloud run services describe ticket-backend-api \
  --region=us-central1 --project=msds-603-victors-demons \
  --format='value(status.url)')
curl "$URL/healthz"
curl -H "Content-Type: application/json" -d '{"ticket_text":"my order never arrived"}' "$URL/predict"
curl -H "Content-Type: application/json" -d '{"ticket_text":"my order never arrived"}' "$URL/tickets"
curl "$URL/tickets?limit=10"
# grab a prediction_id from above, then:
curl -H "Content-Type: application/json" \
  -d '{"prediction_id":"<uuid>","verdict":"thumbs_up"}' "$URL/feedback"
```

Structured logs:
```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="ticket-backend-api" AND (jsonPayload.event="ticket_created" OR jsonPayload.event="feedback_recorded")' \
  --limit=5 --project=msds-603-victors-demons --format=json
```

## CORS

The service is currently public and allows `*` — fine while we have no
auth and the frontend origin is in flux. When the frontend is pinned to
a known URL, tighten `allow_origins` in `backend/api/main.py` and drop
the wildcard.

## File layout

```
backend/
  api/
    __init__.py
    main.py            FastAPI app + routes + startup wiring
    schemas.py         Pydantic request/response models
    model_client.py    HTTP client for the model endpoint (ID-token auth)
    db_client.py       Postgres client + in-memory test double
    config.py          Env-var names + constants
    logging_utils.py   Structured JSON logging
  tests/
    conftest.py        In-memory DB + stub model client + TestClient
    test_health.py
    test_predict.py
    test_tickets.py
    test_feedback.py
  Dockerfile
  cloudbuild.yaml
  requirements.txt
  README.md
```
