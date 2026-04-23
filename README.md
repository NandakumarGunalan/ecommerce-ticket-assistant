# Ecommerce Ticket Triage Assistant

A multi-service GCP system that triages inbound customer-support tickets by predicted priority (low / medium / high / urgent). A support agent pastes a ticket into the web console, the backend routes it through a fine-tuned DistilBERT classifier served on Cloud Run, and the ticket + prediction + agent feedback are persisted to Cloud SQL Postgres for later retraining and analysis.

## Live URLs

- Frontend: https://ticket-frontend-48533944424.us-central1.run.app
- Backend API: https://ticket-backend-api-48533944424.us-central1.run.app
- Model endpoint: https://distilbert-priority-online-48533944424.us-central1.run.app (IAM-restricted; only the backend SA can invoke it)

## Architecture

```
                    +-------------------------+
                    |   Agent (web browser)   |
                    +-----------+-------------+
                                | HTTPS
                                v
                 +--------------+---------------+
                 |  ticket-frontend (Cloud Run) |
                 |  static HTML/CSS/JS          |
                 +--------------+---------------+
                                | fetch JSON
                                v
          +---------------------+----------------------+
          |    ticket-backend-api (Cloud Run, public)  |
          |    FastAPI: /health /tickets /feedback     |
          +----+-----------------------------+---------+
               | ID token (run.invoker)      | Cloud SQL connector
               v                             v
   +-----------+------------+      +---------+-----------------+
   | distilbert-priority-   |      |  Cloud SQL Postgres 15    |
   | online (Cloud Run,     |      |  ticket-assistant-db      |
   | IAM-restricted)        |      |  tickets / predictions /  |
   +-----------+------------+      |  feedback                 |
               | loads             +---------+-----------------+
               v                             ^
   +-----------+-------------+               |
   | Vertex AI Model Registry|               |
   | distilbert-priority     |               |
   +-----------+-------------+               |
               ^                             |
               | registers                   | writes predictions
   +-----------+-------------+     +---------+-----------------+
   | training/ (Vertex AI    |     | distilbert-priority-batch |
   | Custom Training on GPU) |     | (Cloud Run Job)           |
   +-------------------------+     +---------------------------+
```

## Repo layout

- `frontend/` — static HTML/CSS/JS console, served by Cloud Run Service `ticket-frontend`. (Lives on the `feature/frontend-integration` branch; not yet merged to `main`.)
- `backend/` — FastAPI backend + DB schema + apply script. Cloud Run Service `ticket-backend-api`. See `backend/README.md` and `backend/db/README.md`.
- `inference/` — model serving code (shared predictor core used by both online + batch). Cloud Run Service `distilbert-priority-online` and Cloud Run Job `distilbert-priority-batch`. See `inference/PLAN.md` and `inference/ONLINE_PLAN.md`.
- `training/` — DistilBERT fine-tuning pipeline on Vertex AI Custom Training, registers to Vertex Model Registry. See `training/PLAN.md`.
- `synthetic_data/` — synthetic ticket data generation (cross-product of issue/product/sentiment metadata + LLM-generated text). See `synthetic_data/PLAN.md`.
- `scripts/` — legacy utility scripts (pre-GCP; retained for reference).
- `tests/` — legacy top-level tests. Most tests now live next to the code they cover (`backend/tests`, `inference/tests`, `training/tests`, `synthetic_data/tests`).

## Data flow

1. Agent pastes a customer ticket into the frontend and clicks classify.
2. Frontend calls `POST /tickets` on the backend with `{ "ticket_text": "..." }`.
3. Backend attaches a Google-signed ID token (audience = model endpoint URL) and calls the model endpoint's `POST /predict`.
4. Model endpoint loads the DistilBERT artifacts from Vertex AI Model Registry on cold start, scores the ticket, and returns `{predicted_priority, confidence, all_scores, model_version, ...}`.
5. Backend inserts a row into `tickets` and a row into `predictions`, returns the combined response to the frontend.
6. Agent clicks thumbs up / thumbs down; frontend calls `POST /feedback` with the `prediction_id`, which writes to the `feedback` table.
7. `GET /tickets?limit=50` returns the latest prediction per ticket, sorted urgent > high > medium > low, then newest first.

The Cloud Run Job `distilbert-priority-batch` is the same container image as the online service, invoked with a different entrypoint. It reads unscored tickets from Cloud SQL and writes predictions back in bulk. Not currently wired to a scheduler — invoke manually.

## GCP inventory

Project ID: `msds-603-victors-demons` — Region: `us-central1`

| Resource | Name |
| --- | --- |
| Cloud SQL | `ticket-assistant-db` (Postgres 15, `db-f1-micro`, 10 GB) |
| Cloud Run Service | `distilbert-priority-online` |
| Cloud Run Service | `ticket-backend-api` |
| Cloud Run Service | `ticket-frontend` |
| Cloud Run Job | `distilbert-priority-batch` |
| Vertex AI Model Registry | `distilbert-priority` (id `174724492281511936`) |
| Artifact Registry | `ml-repo` (Docker) |
| GCS bucket | `msds603-mlflow-artifacts` (data + model artifacts; bucket name is historical) |
| Secret Manager | `ticket-assistant-db-root-password`, `ticket-assistant-db-app-password` |
| Runtime SA | `inference-runner@msds-603-victors-demons.iam.gserviceaccount.com` |

The runtime SA has `roles/cloudsql.client` project-wide, `roles/secretmanager.secretAccessor` on both DB secrets, and `roles/run.invoker` on `distilbert-priority-online`.

## Quick start (local)

Each service runs in its own terminal. Use the repo virtualenv at `.venv/`, not conda.

Backend (mocked DB, no Postgres needed — tests only):

```bash
.venv/bin/python -m pytest backend/tests -q
```

Backend against live Cloud SQL: see `backend/README.md` — it documents the Cloud SQL Auth Proxy path and the env-var set.

Frontend (static, no build):

```bash
python3 -m http.server 5173 --directory frontend
# then open http://127.0.0.1:5173
# force mock mode (no backend required): http://127.0.0.1:5173?mock=true
```

Model endpoint (smoke test against the deployed Cloud Run service):

```bash
MODEL_URL=$(gcloud run services describe distilbert-priority-online \
  --region=us-central1 --project=msds-603-victors-demons \
  --format='value(status.url)')
TOKEN=$(gcloud auth print-identity-token --audiences="$MODEL_URL")
curl -H "Authorization: Bearer $TOKEN" "$MODEL_URL/healthz"
```

Training smoke test (CPU, tiny subset):

```bash
.venv/bin/python -m pytest training/tests -q
```

## Tech stack

- Python 3.11
- FastAPI, Uvicorn (backend + online inference)
- PyTorch + HuggingFace `transformers` (DistilBERT fine-tune + serving)
- Postgres 15 on Cloud SQL
- Vertex AI (Custom Training + Experiments + Model Registry)
- Cloud Run (Services + Jobs)
- Cloud Build + Artifact Registry (Docker images)
- pytest (unit tests across `backend/`, `inference/`, `training/`, `synthetic_data/`)

## Subsystem docs

- `backend/README.md` — FastAPI backend, routes, env vars, deploy commands
- `backend/db/README.md` — Cloud SQL instance, schema, password rotation, Auth Proxy usage
- `frontend/README.md` — static web console, backend contract, mock mode (lives on `feature/frontend-integration`)
- `inference/PLAN.md` — batch inference design + DB-direct rationale
- `inference/ONLINE_PLAN.md` — online endpoint design, IAM, demo-day playbook
- `training/PLAN.md` — DistilBERT fine-tune pipeline on Vertex AI
- `synthetic_data/PLAN.md` — synthetic ticket generation

## Running tests

```bash
.venv/bin/python -m pytest backend/ inference/ training/ synthetic_data/
```

## Reminders

- Python venv lives at `.venv/` — use `.venv/bin/python`, not conda.
- GCP project is `msds-603-victors-demons`, region `us-central1`.
- The model endpoint is **IAM-restricted**. Hitting it from a browser returns 403; only the backend SA (via ID token) can invoke it.
- Backend uses `/health` (not `/healthz`) because the Cloud Run GFE intercepts `/healthz`. The `/healthz` alias also exists on the backend for parity.
