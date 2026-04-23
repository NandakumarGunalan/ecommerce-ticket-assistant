# Runbook

Operations + demo playbook for the Ecommerce Ticket Triage Assistant. Read `README.md` first for the system overview.

Project: `msds-603-victors-demons` — Region: `us-central1`

## Demo-day playbook

Cloud Run services default to `--min-instances=0` to keep idle cost near zero. Cold starts on the model endpoint are 3-8 seconds (DistilBERT download + load). Pin min-instances ~1 hour before the demo to eliminate cold starts.

Before the demo:

```bash
gcloud run services update distilbert-priority-online \
  --region=us-central1 --min-instances=1 --project=msds-603-victors-demons

gcloud run services update ticket-backend-api \
  --region=us-central1 --min-instances=1 --project=msds-603-victors-demons

gcloud run services update ticket-frontend \
  --region=us-central1 --min-instances=1 --project=msds-603-victors-demons
```

After the demo (revert to scale-to-zero):

```bash
gcloud run services update distilbert-priority-online \
  --region=us-central1 --min-instances=0 --project=msds-603-victors-demons

gcloud run services update ticket-backend-api \
  --region=us-central1 --min-instances=0 --project=msds-603-victors-demons

gcloud run services update ticket-frontend \
  --region=us-central1 --min-instances=0 --project=msds-603-victors-demons
```

Cost of leaving all three pinned: roughly $15/month per service. Revert promptly after the demo.

## Smoke test the live system

End-to-end check against the deployed Cloud Run services. Uses the same commands as the PR #10 / PR #13 verification passes.

```bash
BACKEND=$(gcloud run services describe ticket-backend-api \
  --region=us-central1 --project=msds-603-victors-demons \
  --format='value(status.url)')

# 1. Health (proxies model endpoint's /healthz; returns model_version)
curl -s "$BACKEND/health" | jq .

# 2. Create a ticket (scores + persists)
RESP=$(curl -s -H "Content-Type: application/json" \
  -d '{"ticket_text":"my order never arrived"}' "$BACKEND/tickets")
echo "$RESP" | jq .
PRED_ID=$(echo "$RESP" | jq -r .prediction_id)

# 3. List recent tickets (priority-sorted)
curl -s "$BACKEND/tickets?limit=10" | jq .

# 4. Submit feedback on the prediction above
curl -s -H "Content-Type: application/json" \
  -d "{\"prediction_id\":\"$PRED_ID\",\"verdict\":\"thumbs_up\"}" \
  "$BACKEND/feedback" | jq .
```

Hit the frontend in a browser at the URL in `README.md` and confirm the prediction, ticket list, and thumbs-up/down round-trip through the backend.

Direct check of the model endpoint (requires an ID token — the backend does this automatically):

```bash
MODEL_URL=$(gcloud run services describe distilbert-priority-online \
  --region=us-central1 --project=msds-603-victors-demons \
  --format='value(status.url)')
TOKEN=$(gcloud auth print-identity-token --audiences="$MODEL_URL")
curl -s -H "Authorization: Bearer $TOKEN" "$MODEL_URL/healthz"
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"ticket_text":"my order never arrived"}' "$MODEL_URL/predict"
```

## Deploy a change

All three services share the same build-then-deploy shape: `gcloud builds submit` to push a new image to Artifact Registry, then `gcloud run deploy` to roll it out. Full env-var lists live in each subsystem's README.

### Backend (`ticket-backend-api`)

Local dev loop — run tests with the in-memory DB stub:

```bash
.venv/bin/python -m pytest backend/tests -q
```

Build and deploy:

```bash
gcloud builds submit --config=backend/cloudbuild.yaml \
  --project=msds-603-victors-demons .

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
  --cpu=1 --memory=512Mi --min-instances=0 --max-instances=5 \
  --concurrency=20 --timeout=30s \
  --project=msds-603-victors-demons
```

See `backend/README.md` for the full env-var list and CORS notes.

### Frontend (`ticket-frontend`)

Local dev loop:

```bash
python3 -m http.server 5173 --directory frontend
```

Build and deploy:

```bash
gcloud builds submit --config=frontend/cloudbuild.yaml \
  --project=msds-603-victors-demons .

gcloud run deploy ticket-frontend \
  --image=us-central1-docker.pkg.dev/msds-603-victors-demons/ml-repo/ticket-frontend:latest \
  --region=us-central1 --allow-unauthenticated \
  --cpu=1 --memory=256Mi --min-instances=0 --max-instances=5 \
  --project=msds-603-victors-demons
```

The backend URL the frontend talks to is baked into `frontend/config.js`. See `frontend/README.md`.

### Inference (`distilbert-priority-online` + `distilbert-priority-batch`)

Local dev loop — predictor + FastAPI unit tests:

```bash
.venv/bin/python -m pytest inference/tests -q
```

Build once (shared image for online + batch):

```bash
gcloud builds submit --config=inference/cloudbuild.yaml \
  --project=msds-603-victors-demons .
```

Deploy the online service (`CMD` override launches uvicorn instead of the batch entrypoint):

```bash
gcloud run deploy distilbert-priority-online \
  --image=us-central1-docker.pkg.dev/msds-603-victors-demons/ml-repo/distilbert-priority-inference:latest \
  --region=us-central1 \
  --service-account=inference-runner@msds-603-victors-demons.iam.gserviceaccount.com \
  --no-allow-unauthenticated \
  --cpu=2 --memory=4Gi --min-instances=0 --max-instances=5 \
  --concurrency=4 --timeout=30s \
  --command=uvicorn --args=inference.main:app,--host,0.0.0.0,--port,8080 \
  --project=msds-603-victors-demons
```

Update the batch job image (job config stays put, just swap the image):

```bash
gcloud run jobs update distilbert-priority-batch \
  --image=us-central1-docker.pkg.dev/msds-603-victors-demons/ml-repo/distilbert-priority-inference:latest \
  --region=us-central1 --project=msds-603-victors-demons
```

Execute the batch job:

```bash
gcloud run jobs execute distilbert-priority-batch \
  --region=us-central1 --project=msds-603-victors-demons
```

See `inference/PLAN.md` and `inference/ONLINE_PLAN.md` for full config tables and design rationale.

## Retrain the model

Full procedure: `training/PLAN.md`. Three-bullet summary:

- Ensure training data is in GCS at `gs://msds603-mlflow-artifacts/data/tickets/v{N}/tickets.csv`. Generate new data via `synthetic_data/generate.py` if needed.
- `.venv/bin/python training/launch.py` submits a Vertex AI Custom Training job (n1-standard-4 + 1x T4, ~10-15 min, ~$0.10).
- The job logs to Vertex AI Experiments and registers a new version under `distilbert-priority` in Vertex AI Model Registry. The online endpoint and batch job pick up the new default version on their next cold start (set `MODEL_VERSION` env var to pin a specific version).

## Common ops

View recent structured logs per service:

```bash
# Backend (business events)
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="ticket-backend-api" AND (jsonPayload.event="ticket_created" OR jsonPayload.event="feedback_recorded" OR jsonPayload.event="model_endpoint_error")' \
  --limit=20 --project=msds-603-victors-demons --format=json

# Model endpoint
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="distilbert-priority-online"' \
  --limit=20 --project=msds-603-victors-demons --format=json

# Batch job
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="distilbert-priority-batch"' \
  --limit=20 --project=msds-603-victors-demons --format=json
```

Rotate the app DB password:

```bash
# 1. Generate a new password and update the user
NEW_PW=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
gcloud sql users set-password app_user \
  --instance=ticket-assistant-db --password="$NEW_PW" \
  --project=msds-603-victors-demons

# 2. Write a new version to the secret
printf '%s' "$NEW_PW" | gcloud secrets versions add ticket-assistant-db-app-password \
  --data-file=- --project=msds-603-victors-demons

# 3. Force the backend to pick up the new secret version
gcloud run services update ticket-backend-api \
  --region=us-central1 --project=msds-603-victors-demons \
  --update-env-vars="DB_PASSWORD_ROTATED_AT=$(date +%s)"

unset NEW_PW
```

Do not log or echo `$NEW_PW` to shared terminals.

Connect to Cloud SQL locally: see `backend/db/README.md` for the Cloud SQL Auth Proxy recipe.

Check what's deployed:

```bash
gcloud run services list --project=msds-603-victors-demons
gcloud run revisions list --service=ticket-backend-api \
  --region=us-central1 --project=msds-603-victors-demons
```

## Known issues / tech debt

- `/healthz` on the inference online service is intercepted by the Cloud Run GFE in some configurations. The backend now uses `/health` (the alias added in PR #13) to proxy through reliably.
- Frontend and backend are both public (`--allow-unauthenticated`, CORS `*`). Fine for a class demo. Add IAM + OAuth + a pinned CORS origin before this is exposed to real users.
- No retraining pipeline or scheduler is wired up. Retrains are manual via `training/launch.py`. Cloud Scheduler + Workflows would be a straightforward follow-up.
- No monitoring dashboards. Structured logs exist; a Looker Studio view on top of the `predictions` and `feedback` tables would surface model quality over time.
- CSV upload path (bulk ticket ingestion) is scoped out. Batch inference runs against whatever sits in the `tickets` table.
- The frontend is not currently merged to `main`. It lives on `feature/frontend-integration`. Deploys happen from that branch until it's merged.

## Emergency: something is broken

- Backend returns 502 on `/health` -> model endpoint is probably down or returning non-200. Check it:
  ```bash
  gcloud run services describe distilbert-priority-online \
    --region=us-central1 --project=msds-603-victors-demons
  ```
  Then hit its `/healthz` directly with an ID token (see smoke test section).
- Frontend shows "Endpoint unreachable" -> backend is down, CORS is misconfigured, or `/health` chain is broken. Open the browser devtools network tab and inspect the failing request.
- `POST /tickets` returns 500 -> query Cloud Logging for `jsonPayload.event="model_endpoint_error"` on `ticket-backend-api`. Common causes: model endpoint cold-start timeout, expired ID token audience mismatch, DB write failure.
- DB connections failing (`FATAL: password authentication failed for user "app_user"`) -> the `ticket-assistant-db-app-password` secret and the actual Postgres user password have drifted. Re-run the rotation steps above or re-run `backend/db/apply_schema.sh` which normalizes the `app_user` password to match the secret.
- Cloud SQL instance status anything other than `RUNNABLE`:
  ```bash
  gcloud sql instances describe ticket-assistant-db --project=msds-603-victors-demons
  ```
  If stopped, start it:
  ```bash
  gcloud sql instances patch ticket-assistant-db \
    --activation-policy=ALWAYS --project=msds-603-victors-demons
  ```
- Roll back a bad Cloud Run deploy to the previous revision:
  ```bash
  gcloud run services update-traffic ticket-backend-api \
    --to-revisions=<previous-revision-name>=100 \
    --region=us-central1 --project=msds-603-victors-demons
  ```
