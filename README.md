# Turbo Triage (Ecommerce Ticket Triage Assistant)

> Product name: **Turbo Triage**. The repo / GCP project / image names retain the
> original `ecommerce-ticket-*` identifiers — that's the historical name and
> changing it now would be a code-and-infra rename for no real benefit.

A multi-service GCP system that triages inbound customer-support tickets by predicted priority (low / medium / high / urgent). A support agent signs in with Google, pastes (or uploads a CSV of) tickets into the web console, the backend routes single tickets through a fine-tuned DistilBERT classifier served on Cloud Run, and the ticket + prediction + agent feedback are persisted to Cloud SQL Postgres — scoped to the authenticated user — for later retraining and analysis. Bulk CSV-uploaded tickets land as "pending" rows and are scored by the nightly batch job.

## Live URLs

- Frontend: https://tickets.holderbein.dev (custom domain — primary). The default Firebase Hosting URLs continue to work: `https://msds-603-victors-demons.web.app` and `https://msds-603-victors-demons.firebaseapp.com`.
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
                 |  Firebase Hosting            |
                 |  static HTML/CSS/JS console  |
                 +---+----------------------+---+
                     |                      |
        Google Sign-In (GIS) +              | fetch JSON + Bearer <Firebase ID token>
        signInWithCredential                v
                     |       +--------------+---------------+
                     |       |  ticket-backend-api          |
                     |       |  (Cloud Run, public ingress) |
                     |       |  FastAPI + firebase-admin    |
                     |       |  verifies ID token on every  |
                     |       |  protected route, rate-limits|
                     |       |  50 req/min/user, scopes all |
                     |       |  DB ops by user.uid          |
                     v       +---+-----------------------+--+
          +----------+---+       | ID token (run.invoker)| Cloud SQL connector
          | Firebase Auth|       v                       v
          | (Google IdP) |  +----+---------------+  +----+----------------------+
          +--------------+  | distilbert-        |  |  Cloud SQL Postgres 15    |
                            | priority-online    |  |  ticket-assistant-db      |
                            | (Cloud Run,        |  |  tickets (user_id,        |
                            | IAM-restricted)    |  |    resolved_at) /         |
                            +----+---------------+  |  predictions / feedback / |
                                 | loads            |  rate_limit_counters      |
                                 v                  +---+-----------------------+
                            +----+----------------+     ^
                            | Vertex AI Model Reg |     |
                            | distilbert-priority |     |
                            +----+----------------+     | writes predictions
                                 ^                      |
                                 | registers            |
                            +----+----------------+  +--+--------------------------+
                            | training/ (Vertex   |  | distilbert-priority-batch   |
                            | AI Custom Training) |  | (Cloud Run Job, same image) |
                            +---------------------+  +-----------------------------+
```

## Repo layout

- `frontend/` — static HTML/CSS/JS console, deployed via Firebase Hosting (site `msds-603-victors-demons`). Authenticates users with Firebase Auth (Google Sign-In, GIS + `signInWithCredential`).
- `backend/` — FastAPI backend + DB schema + migrations + apply scripts. Cloud Run Service `ticket-backend-api`. Verifies Firebase ID tokens, enforces per-user rate limits, scopes all DB operations by `user.uid`. See `backend/README.md` and `backend/db/README.md`.
- `inference/` — model serving code (shared predictor core used by both online + batch). Cloud Run Service `distilbert-priority-online` and Cloud Run Job `distilbert-priority-batch`. See `inference/PLAN.md` and `inference/ONLINE_PLAN.md`.
- `training/` — DistilBERT fine-tuning pipeline on Vertex AI Custom Training, registers to Vertex Model Registry. See `training/PLAN.md`.
- `synthetic_data/` — synthetic ticket data generation (cross-product of issue/product/sentiment metadata + LLM-generated text). See `synthetic_data/PLAN.md`.
- `scripts/` — legacy utility scripts (pre-GCP; retained for reference).
- `tests/` — legacy top-level tests. Most tests now live next to the code they cover (`backend/tests`, `inference/tests`, `training/tests`, `synthetic_data/tests`, `frontend/tests`).

## Data flow

1. User opens the frontend and signs in with Google. The page uses Google Identity Services (GIS) to fetch an access token, then exchanges it via `signInWithCredential` to obtain a Firebase session. (We do not use `signInWithPopup` / `signInWithRedirect` — see `frontend/README.md` for the storage-partitioning background.)
2. On every protected call the frontend fetches a fresh Firebase ID token (`auth.currentUser.getIdToken()`) and attaches it as `Authorization: Bearer <token>`.
3. The backend verifies the token with `firebase-admin` (`verify_id_token`), extracts `user.uid` / `email` / `name`, and runs a per-request rate-limit check against the Postgres-backed `rate_limit_counters` table (50 req/min/user; a 429 with `Retry-After: 60` is returned on breach).
4. `POST /tickets`: backend calls the IAM-restricted model endpoint using a Google-signed ID token (audience = model endpoint URL), writes a `tickets` row and a `predictions` row scoped to `user.uid`, and returns the combined record.
5. `GET /tickets`: returns the caller's tickets only; by default excludes rows with a non-null `resolved_at` (pass `?include_resolved=true` to include them). Sorted urgent > high > medium > low, then newest first.
6. Tickets can be resolved (`POST /tickets/{id}/resolve`) and reopened (`POST /tickets/{id}/unresolve`); resolution state is stored as a `resolved_at` timestamp on the ticket row.
7. `POST /feedback` records a thumbs-up / thumbs-down against a `prediction_id`.
8. `POST /tickets/upload-csv` accepts a multipart CSV file and inserts up to 500 rows as **pending** tickets (`source='csv'`) — no synchronous scoring. Pending tickets show in the Tickets tab with a grey "Pending" badge until the nightly batch job scores them.

The Cloud Run Job `distilbert-priority-batch` is the same container image as the online service, invoked with a different entrypoint. It reads unscored tickets from Cloud SQL via IAM auth (separate user from the backend — see "Database users" in `backend/db/README.md`) and writes predictions back in bulk. Wired to **Cloud Scheduler** (`distilbert-batch-nightly`) — runs `0 10 * * *` UTC (= 2 AM Pacific). See `RUNBOOK.md` for the manual-trigger and pause/resume commands.

## API surface

All routes are served by `ticket-backend-api`. Only `GET /health` is public; every other route requires `Authorization: Bearer <firebase_id_token>` and is subject to the 50 req/min/user rate limit.

| Method | Path | Notes |
| --- | --- | --- |
| GET  | `/health` | Public. Proxies the model endpoint's `/healthz`; returns `model_version`. |
| GET  | `/me` | Authed. Returns `{uid, email, display_name}` from the verified token. |
| POST | `/predict` | Authed. Stateless score — no DB write. |
| POST | `/tickets` | Authed. Score + persist `tickets` + `predictions` rows scoped to `user.uid`. |
| GET  | `/tickets?limit=50&include_resolved=false` | Authed. Caller's tickets only. `include_resolved` defaults to `false`. |
| POST | `/tickets/{id}/resolve` | Authed. Marks the ticket resolved (owner-only). |
| POST | `/tickets/{id}/unresolve` | Authed. Clears `resolved_at`. |
| POST | `/tickets/upload-csv` | Authed. Multipart `file` field. Inserts up to 500 pending tickets (`source='csv'`); skips rows shorter than 5 chars. Returns `{accepted, skipped}`. |
| POST | `/feedback` | Authed. Thumbs up/down against a `prediction_id`. |

The backend also registers `/healthz` as an alias to `/health`, but Cloud Run's ingress intercepts `/healthz` in some configurations — use `/health` for smoke tests.

## GCP inventory

Project ID: `msds-603-victors-demons` — Region: `us-central1`

| Resource | Name |
| --- | --- |
| Cloud SQL | `ticket-assistant-db` (Postgres 15, `db-f1-micro`, 10 GB). **Required flag:** `cloudsql.iam_authentication=on` (without it the batch job's IAM auth fails — see `RUNBOOK.md`). |
| Cloud SQL users | `app_user` (password, used by backend) and `inference-runner@msds-603-victors-demons.iam` (IAM, used by batch job). See `backend/db/README.md` for the two-user model. |
| Cloud Run Service | `distilbert-priority-online` |
| Cloud Run Service | `ticket-backend-api` |
| Cloud Run Job | `distilbert-priority-batch` |
| Cloud Scheduler | `distilbert-batch-nightly` (location `us-central1`, schedule `0 10 * * *` UTC, target = batch job v2 admin endpoint) |
| Firebase Hosting site | `msds-603-victors-demons` (default URLs `.firebaseapp.com` + `.web.app`); custom domain **`tickets.holderbein.dev`** (Porkbun DNS, see `RUNBOOK.md`) |
| Firebase Web App | `ticket-console` (App ID `1:48533944424:web:1caab7a98902277a3823dd`) |
| Firebase Auth | Google provider; authorized domains: `localhost`, `msds-603-victors-demons.firebaseapp.com`, `msds-603-victors-demons.web.app`, `tickets.holderbein.dev` |
| OAuth web client | `48533944424-ovpv2i1f9aecvr30jj1ipgg9eo2ho8l1`. Authorized JS origins + redirect URIs must include all hosts the frontend is served from (see `RUNBOOK.md` "Custom domain"). |
| Vertex AI Model Registry | `distilbert-priority` (id `174724492281511936`) |
| Artifact Registry | `ml-repo` (Docker) |
| GCS bucket | `msds603-mlflow-artifacts` (data + model artifacts; bucket name is historical) |
| Secret Manager | `ticket-assistant-db-root-password`, `ticket-assistant-db-app-password` |
| Runtime SA | `inference-runner@msds-603-victors-demons.iam.gserviceaccount.com` |
| Firebase Admin SA | `firebase-adminsdk-fbsvc@msds-603-victors-demons.iam.gserviceaccount.com` (used by the backend to verify ID tokens via ADC) |

The runtime SA `inference-runner` has: `roles/cloudsql.client` and `roles/cloudsql.instanceUser` (DB), `roles/secretmanager.secretAccessor` on both DB secrets, `roles/run.invoker` on `distilbert-priority-online` (so the backend can call the model) **and** on `distilbert-priority-batch` (so Cloud Scheduler can trigger the batch job). The Cloud Scheduler service agent also has `roles/iam.serviceAccountTokenCreator` on `inference-runner` so it can mint OAuth tokens for the scheduled invocation.

> Note: the previous Cloud Run frontend service `ticket-frontend` has been **deleted**. The frontend is served by Firebase Hosting; do not redeploy the Cloud Run service.

## Quick start (local)

Each service runs in its own terminal. Use the repo virtualenv at `.venv/`, not conda.

Backend (mocked DB + stub model client, no Postgres needed — tests only):

```bash
.venv/bin/python -m pytest backend/tests -q
```

Backend against live Cloud SQL: see `backend/README.md` — it documents the Cloud SQL Auth Proxy path and the env-var set.

Frontend (static, no build). Canonical local dev loop uses Python's built-in HTTP server:

```bash
python3 -m http.server 5173 --directory frontend
# then open http://127.0.0.1:5173
# force mock mode (no backend, no sign-in): http://127.0.0.1:5173?mock=true
```

`localhost` is already on Firebase's authorized-domains list, so Google Sign-In works out of the box. (`firebase emulators:start --only hosting` also works if you prefer the Firebase CLI — same static assets, same origin semantics.)

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
- Firebase Auth (Google Sign-In via GIS + `signInWithCredential`) + `firebase-admin` server-side token verification
- Firebase Hosting (static frontend)
- Vertex AI (Custom Training + Experiments + Model Registry)
- Cloud Run (Services + Jobs)
- Cloud Build + Artifact Registry (Docker images)
- pytest (unit tests across `backend/`, `inference/`, `training/`, `synthetic_data/`, `frontend/`)

## Subsystem docs

**Currently authoritative:**
- `backend/README.md` — FastAPI backend: routes, env vars, auth, rate limiting, deploy commands, CSV upload, two-user DB model
- `backend/db/README.md` — Cloud SQL instance: schema, migrations, IAM database user setup, `cloudsql.iam_authentication` flag, Auth Proxy usage, password rotation, two-user model
- `frontend/README.md` — Turbo Triage web console: Firebase Auth + GIS sign-in, backend contract, mock mode, custom-domain config, cold-start retry semantics, Firebase Hosting deploy
- `RUNBOOK.md` — Day-2 operations: nightly batch scheduler, custom domain, IAM-auth gotchas, emergency procedures

**Historical design docs** (kept for context; production may have drifted — read the source files they reference for current behaviour):
- `inference/PLAN.md` — batch inference design + DB-direct rationale (the "Predictions Table Contract" section is **superseded** — the production schema is in `backend/db/README.md`)
- `inference/ONLINE_PLAN.md` — online endpoint design + IAM playbook
- `training/PLAN.md` — DistilBERT fine-tune pipeline on Vertex AI
- `synthetic_data/PLAN.md` — synthetic ticket generation

## Running tests

```bash
.venv/bin/python -m pytest backend/ inference/ training/ synthetic_data/
```

## Known issues / tech debt

- Cloud Run's ingress intercepts `/healthz` on some service configurations; the backend exposes `/health` as the canonical alias. Use `/health` for smoke tests and uptime probes.
- Backend CORS allowlist is a fixed set of prod origins + `localhost` (see `backend/api/main.py::_install_cors`). Firebase Hosting preview channels (`<site>--<channel>-<hash>.web.app`) are not in the allowlist; add them explicitly or redeploy the backend with the preview origin before testing against a preview.
- No automated retraining pipeline. Retrains are manual via `.venv/bin/python training/launch.py`. Cloud Scheduler + Workflows would be a straightforward follow-up.
- No monitoring dashboards beyond Cloud Logging. A Looker Studio view over `predictions` + `feedback` is a natural next step.
- The `predictions` table has no `UNIQUE(ticket_id, model_version)` constraint. The batch job is therefore not idempotent — re-running it can write duplicate prediction rows for the same ticket. The backend's `list_tickets` collapses to the most recent prediction via `DISTINCT ON`, so duplicates are harmless at read time, but they bloat the table. A future migration could add the constraint and switch the batch back to upsert semantics (the `inference/db.py` SQL templates already document this path).

## Reminders

- Python venv lives at `.venv/` — use `.venv/bin/python`, not conda.
- GCP project is `msds-603-victors-demons`, region `us-central1`.
- The model endpoint is **IAM-restricted**. Hitting it from a browser returns 403; only the backend SA (via Google-signed ID token) can invoke it.
- Backend uses `/health` (not `/healthz`) because the Cloud Run GFE intercepts `/healthz`. The `/healthz` alias also exists on the backend for parity.
- Firebase web config values (`apiKey`, `authDomain`, etc.) in `frontend/config.js` are public identifiers, not secrets. Security is enforced by backend token verification and Firebase's authorized-domains list.
