# Runbook

Operations + demo playbook for the Ecommerce Ticket Triage Assistant. Read `README.md` first for the system overview.

Project: `msds-603-victors-demons` — Region: `us-central1`

## Demo-day playbook

Cloud Run services default to `--min-instances=0` to keep idle cost near zero. The model endpoint's cold start is 3-8 seconds (DistilBERT download + load), which is the one place where pinning min-instances ~1 hour before the demo is worth doing. The backend's cold start is sub-second, so it can stay at min=0.

The frontend is now on **Firebase Hosting** — it's global CDN, no min-instances knob, nothing to pin.

Before the demo:

```bash
gcloud run services update distilbert-priority-online \
  --region=us-central1 --min-instances=1 --project=msds-603-victors-demons
```

(Optional — usually unnecessary.) Pin the backend too:

```bash
gcloud run services update ticket-backend-api \
  --region=us-central1 --min-instances=1 --project=msds-603-victors-demons
```

After the demo (revert to scale-to-zero):

```bash
gcloud run services update distilbert-priority-online \
  --region=us-central1 --min-instances=0 --project=msds-603-victors-demons

gcloud run services update ticket-backend-api \
  --region=us-central1 --min-instances=0 --project=msds-603-victors-demons
```

Cost of leaving the model endpoint pinned: roughly $15/month. Revert promptly after the demo.

> Note: the previous Cloud Run frontend service `ticket-frontend` has been **deleted**. Do not try to pin or redeploy it.

## Smoke test the live system

End-to-end check against the deployed Cloud Run services. Every route except `/health` requires a Firebase ID token.

### Mint a test token

There is no CLI-only path to mint a Firebase user ID token. The simplest route:

1. Open the frontend (`https://msds-603-victors-demons.firebaseapp.com`) in a browser and sign in with Google.
2. Open DevTools -> Console and run:
   ```js
   await window.__firebase.auth.currentUser.getIdToken()
   ```
3. Copy the returned JWT. Export it in your shell:
   ```bash
   export TOKEN='<paste-the-jwt-here>'
   ```

ID tokens expire after ~1 hour. If a curl starts returning `401`, refresh the page and repeat.

### Curl sequence

```bash
BACKEND=$(gcloud run services describe ticket-backend-api \
  --region=us-central1 --project=msds-603-victors-demons \
  --format='value(status.url)')

AUTH="Authorization: Bearer $TOKEN"
JSON="Content-Type: application/json"

# 1. Public health (no token; proxies the model endpoint's /healthz)
curl -s "$BACKEND/health" | jq .

# 2. Confirm the token is accepted + see your decoded claims
curl -s -H "$AUTH" "$BACKEND/me" | jq .

# 3. Create a ticket (scores + persists, scoped to your uid)
RESP=$(curl -s -H "$AUTH" -H "$JSON" \
  -d '{"ticket_text":"my order never arrived"}' "$BACKEND/tickets")
echo "$RESP" | jq .
TICKET_ID=$(echo "$RESP" | jq -r .ticket_id)
PRED_ID=$(echo "$RESP" | jq -r .prediction_id)

# 4. List your recent tickets (priority-sorted; resolved hidden by default)
curl -s -H "$AUTH" "$BACKEND/tickets?limit=10" | jq .

# 5. Submit feedback on the prediction above
curl -s -H "$AUTH" -H "$JSON" \
  -d "{\"prediction_id\":\"$PRED_ID\",\"verdict\":\"thumbs_up\"}" \
  "$BACKEND/feedback" | jq .

# 6. Resolve the ticket
curl -s -H "$AUTH" -X POST "$BACKEND/tickets/$TICKET_ID/resolve" | jq .

# 7. Verify it is hidden by default and shown when include_resolved=true
curl -s -H "$AUTH" "$BACKEND/tickets?limit=10" | jq '[.[] | .ticket_id]'
curl -s -H "$AUTH" "$BACKEND/tickets?limit=10&include_resolved=true" | jq '[.[] | .ticket_id]'

# 8. Reopen it
curl -s -H "$AUTH" -X POST "$BACKEND/tickets/$TICKET_ID/unresolve" | jq .
```

Also hit the deployed frontend in a browser, sign in, create/resolve/reopen a ticket, and confirm the UI matches what the curls reported.

Direct check of the model endpoint (IAM-restricted — uses a Google-signed ID token, not a Firebase token):

```bash
MODEL_URL=$(gcloud run services describe distilbert-priority-online \
  --region=us-central1 --project=msds-603-victors-demons \
  --format='value(status.url)')
GTOKEN=$(gcloud auth print-identity-token --audiences="$MODEL_URL")
curl -s -H "Authorization: Bearer $GTOKEN" "$MODEL_URL/healthz"
curl -s -H "Authorization: Bearer $GTOKEN" -H "Content-Type: application/json" \
  -d '{"ticket_text":"my order never arrived"}' "$MODEL_URL/predict"
```

## Deploy a change

### Backend (`ticket-backend-api`)

Local dev loop — run tests with the in-memory DB stub + stub verifier:

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

`--allow-unauthenticated` is correct: Cloud Run ingress is public, but the app enforces Firebase ID-token auth on every route except `/health`. See `backend/README.md` for the full env-var list, CORS allowlist, and notes on the rate limiter.

### Frontend (Firebase Hosting)

Local dev loop:

```bash
python3 -m http.server 5173 --directory frontend
# http://127.0.0.1:5173  (localhost is on Firebase's authorized-domains list)
```

Deploy (no build step — static assets; `firebase.json` lives in `frontend/`):

```bash
cd frontend
firebase deploy --only hosting --project=msds-603-victors-demons
```

The site URLs are `https://msds-603-victors-demons.firebaseapp.com` and `https://msds-603-victors-demons.web.app` (equivalent aliases). No custom domain configured.

> The previous Cloud Run frontend service `ticket-frontend` has been **deleted**. Do not redeploy it. The `frontend/Dockerfile` and `frontend/cloudbuild.yaml` remain in the repo for historical reference only.

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

### Startup CPU boost on the model endpoint

The online service has `run.googleapis.com/startup-cpu-boost: true` set on the service — free cold-start acceleration (2x CPU during container startup, normal after). **`gcloud run deploy` preserves this annotation across deploys**, so routine redeploys don't lose it. It only gets cleared if someone runs `gcloud run services replace` with a YAML that omits it, or passes `--no-cpu-boost` explicitly.

Verify after a deploy:

```bash
gcloud run services describe distilbert-priority-online \
  --region=us-central1 --project=msds-603-victors-demons \
  --format="value(spec.template.metadata.annotations.'run.googleapis.com/startup-cpu-boost')"
# Expect: true
```

If it ever shows empty, re-enable:

```bash
gcloud beta run services update distilbert-priority-online \
  --region=us-central1 --cpu-boost --project=msds-603-victors-demons
```

(Requires the `beta` component: `gcloud components install beta`.)

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

### View recent structured logs per service

```bash
# Backend (business events)
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="ticket-backend-api" AND (jsonPayload.event="ticket_created" OR jsonPayload.event="feedback_recorded" OR jsonPayload.event="ticket_resolved" OR jsonPayload.event="ticket_unresolved" OR jsonPayload.event="model_endpoint_error")' \
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

### Rotate the app DB password

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

### Connect to Cloud SQL locally

See `backend/db/README.md` for the Cloud SQL Auth Proxy recipe.

### Check what's deployed

```bash
gcloud run services list --project=msds-603-victors-demons
gcloud run revisions list --service=ticket-backend-api \
  --region=us-central1 --project=msds-603-victors-demons
firebase hosting:sites:list --project=msds-603-victors-demons
```

### User management (Firebase Auth)

Firebase doesn't expose `gcloud`-level user management, but the Identity Toolkit REST API does. You need an OAuth2 access token for an account with Firebase admin rights:

```bash
# Log in once; the CLI caches a refresh token.
firebase login

# Extract the access token from the firebase-tools configstore.
ACCESS_TOKEN=$(python3 -c '
import json, pathlib
p = pathlib.Path.home() / ".config" / "configstore" / "firebase-tools.json"
print(json.loads(p.read_text())["tokens"]["access_token"])
')

PROJECT=msds-603-victors-demons

# List users (first page, up to 1000)
curl -s -X POST \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"returnUserInfo": true}' \
  "https://identitytoolkit.googleapis.com/v1/projects/$PROJECT/accounts:query" | jq '.userInfo[] | {localId, email, displayName, createdAt}'

# Delete a specific user by uid (repeat --data for multiple local IDs)
curl -s -X POST \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"localIds":["<firebase-uid-to-delete>"]}' \
  "https://identitytoolkit.googleapis.com/v1/projects/$PROJECT/accounts:batchDelete" | jq .

unset ACCESS_TOKEN
```

Deleting a Firebase user does not cascade into our Postgres tables — their `tickets`, `predictions`, and `feedback` rows stay behind for retraining. To also drop their data:

```sql
DELETE FROM tickets WHERE user_id = '<firebase-uid>';
-- predictions + feedback cascade via ON DELETE CASCADE
DELETE FROM rate_limit_counters WHERE user_id = '<firebase-uid>';
```

### Bump the rate limit

The default is 50 req/min/user, set as `DEFAULT_LIMIT_PER_MINUTE` in `backend/api/rate_limit.py`. Two options:

- **Change the default** for all call sites: edit `DEFAULT_LIMIT_PER_MINUTE` in `backend/api/rate_limit.py`, rebuild, redeploy the backend (see "Deploy a change -> Backend" above).
- **Change it at a specific attachment point**: pass `limit=N` to `make_rate_limit_dep(store, limit=N)`. Note that `backend/api/main.py` currently inlines the rate-limit logic in `rate_limited_user(...)` rather than calling `make_rate_limit_dep` (so it picks up `_state["db"]` via `Depends(get_db)` for test override compatibility); bumping there means editing the `DEFAULT_LIMIT_PER_MINUTE` reference in that function.

### Check a user's open vs resolved ticket counts

With a psql session open against `ticket_assistant` (see `backend/db/README.md` for the Auth Proxy recipe):

```sql
SELECT
  user_id,
  COUNT(*) FILTER (WHERE resolved_at IS NULL) AS open_tickets,
  COUNT(*) FILTER (WHERE resolved_at IS NOT NULL) AS resolved_tickets
FROM tickets
GROUP BY user_id
ORDER BY open_tickets DESC;

-- Or for a specific user:
SELECT id, resolved_at, created_at
FROM tickets
WHERE user_id = '<firebase-uid>'
ORDER BY created_at DESC;
```

### Apply a new migration

Migrations live in `backend/db/migrations/`. They are one-shot (unlike the idempotent `schema.sql`):

```bash
# Apply a specific migration file:
bash backend/db/apply_migration.sh backend/db/migrations/002_add_ticket_resolved.sql

# With no argument, the script applies 001_add_user_accounts.sql.
```

After applying, update `backend/db/schema.sql` so a fresh DB built with `apply_schema.sh` ends up in the same state.

## Nightly batch scheduler

- Cloud Scheduler job: `distilbert-batch-nightly` (location: `us-central1`)
- Schedule: `0 10 * * *` UTC (= 2 AM Pacific Standard Time)
- Target: POST to the Cloud Run Job v2 admin endpoint:
  `https://run.googleapis.com/v2/projects/msds-603-victors-demons/locations/us-central1/jobs/distilbert-priority-batch:run`
- Auth: OAuth, service account `inference-runner@msds-603-victors-demons.iam.gserviceaccount.com`
- Required IAM (one-time setup):
  - `inference-runner` SA needs `roles/run.invoker` on the job
  - Cloud Scheduler service agent (`service-48533944424@gcp-sa-cloudscheduler.iam.gserviceaccount.com`) needs `roles/iam.serviceAccountTokenCreator` on `inference-runner`

Manual trigger:

```bash
gcloud scheduler jobs run distilbert-batch-nightly \
  --location=us-central1 --project=msds-603-victors-demons
```

Pause/resume:

```bash
gcloud scheduler jobs pause distilbert-batch-nightly \
  --location=us-central1 --project=msds-603-victors-demons
gcloud scheduler jobs resume distilbert-batch-nightly \
  --location=us-central1 --project=msds-603-victors-demons
```

## Custom domain (`tickets.holderbein.dev`)

The frontend is reachable at:

- https://msds-603-victors-demons.web.app (default)
- https://msds-603-victors-demons.firebaseapp.com (default alias)
- https://tickets.holderbein.dev (custom)

Custom domain pieces (all required, all already configured):

1. Firebase Hosting domain registered (REST: POST sites/.../domains)
2. DNS at Porkbun: A record `tickets -> 199.36.158.100`,
   TXT `tickets -> hosting-site=msds-603-victors-demons`,
   TXT `_acme-challenge.tickets -> <Let's Encrypt token from Firebase>`
3. Firebase Auth authorized domains list includes `tickets.holderbein.dev`
4. Google OAuth web client (`48533944424-ovpv2i1f9aecvr30jj1ipgg9eo2ho8l1`)
   has `https://tickets.holderbein.dev` in BOTH "Authorized JavaScript
   origins" AND "Authorized redirect URIs"
   (the redirect URI must be `https://tickets.holderbein.dev/__/auth/handler`)
5. Backend CORS allowlist includes `https://tickets.holderbein.dev`

Forgetting any one of these breaks sign-in or API calls from the custom
domain only — the default URLs continue to work, which makes the bug
easy to miss in testing.

## Database users (two-user model)

The Cloud SQL Postgres instance has two distinct application users:

- `app_user` — used by the backend API (password auth, secret in Secret
  Manager: `ticket-assistant-db-app-password`)
- `inference-runner@msds-603-victors-demons.iam` — used by the batch
  inference Cloud Run Job (IAM auth, no password)

This split was intentional in the original design (no secrets for batch),
but does mean two different failure modes when DB connectivity breaks.
When debugging "the DB is broken," check which path:

- 502 from `/tickets` -> backend `app_user` path
- Cloud Run Job exit 1 with `SQLSTATE 28000` -> IAM-auth path

## Cloud SQL IAM auth requires a database flag

The Cloud SQL instance must have `cloudsql.iam_authentication=on` in
its database flags for the IAM-auth path to work. Without it, the
batch job fails with `Cloud SQL IAM service account authentication failed`
even when the IAM Postgres user, IAM project roles, and grants are all
correct. The flag is OFF by default for CLI-created instances; only
the Console enables it on creation.

To verify:

```bash
gcloud sql instances describe ticket-assistant-db \
  --project=msds-603-victors-demons \
  --format='value(settings.databaseFlags)'
```

If absent, set it (will restart the instance, ~1-2 min downtime):

```bash
gcloud sql instances patch ticket-assistant-db \
  --project=msds-603-victors-demons \
  --database-flags=cloudsql.iam_authentication=on
```

Note: `--database-flags` REPLACES the entire flag set, so include any
other flags too if there are any.

## Known issues / tech debt

- **`/healthz` is GFE-intercepted** on the Cloud Run backend for some service configurations. Use `/health` (the canonical alias) for smoke tests and uptime probes.
- **CORS allowlist is fixed** to the prod Firebase Hosting origins + `localhost` (see `backend/api/main.py::_install_cors`). Firebase Hosting preview channels (`<site>--<channel>-<hash>.web.app`) are not in the list; add the preview origin to `_install_cors` and redeploy the backend before testing against a preview channel.
- **No automated retraining pipeline.** Retrains are manual via `.venv/bin/python training/launch.py`. Remediation: Workflows -> the existing `launch.py` entry point would be a minimal follow-up.
- **No monitoring dashboards** beyond Cloud Logging. Remediation: a Looker Studio view over `predictions` + `feedback` to show model quality over time.

## Emergency: something is broken

### Authentication failure modes

- **Users stuck in a redirect loop on sign-in.** Should not happen any more — the frontend uses Google Identity Services + `signInWithCredential`, which does not go through the Firebase auth iframe or redirect flow. If it does recur: check `.claude/FIREBASE_AUTH_BUG.md` and the `.claude/diagnosis_*.md` files for the 2024-2025 Chrome storage-partitioning context. Most likely cause would be accidentally reintroducing `signInWithPopup` or `signInWithRedirect` to the code.
- **Users get 401 on every call.** Check backend init logs for firebase_admin SDK failures:
  ```bash
  gcloud logging read \
    'resource.type="cloud_run_revision" AND resource.labels.service_name="ticket-backend-api" AND severity>=ERROR' \
    --limit=30 --project=msds-603-victors-demons --format=json
  ```
  Also check the browser's DevTools Network tab — if the CORS preflight (`OPTIONS`) is failing, the request origin is not in the backend allowlist. Fix: add the origin to `_install_cors` in `backend/api/main.py` and redeploy.
- **Users get 429 immediately on first click.** The `rate_limit_counters` table has stuck rows. Either their minute window has drifted (unlikely — we use `datetime.now(timezone.utc).replace(second=0, microsecond=0)`) or something retried thousands of times and blew the cap. Inspect and, if necessary, truncate:
  ```sql
  SELECT user_id, window_start_minute, count
  FROM rate_limit_counters
  WHERE count > 50
  ORDER BY count DESC LIMIT 20;

  -- Nuclear option (safe; counters recreate themselves):
  TRUNCATE rate_limit_counters;
  ```

### Service failure modes

- **Backend returns 502 on `/health`** -> model endpoint is down or returning non-200. Check it:
  ```bash
  gcloud run services describe distilbert-priority-online \
    --region=us-central1 --project=msds-603-victors-demons
  ```
  Then hit its `/healthz` directly with a Google-signed ID token (see smoke-test section).
- **Frontend shows "Endpoint unreachable"** -> backend is down, CORS is misconfigured for the current origin, or the `/health` chain is broken. Open DevTools Network and inspect the failing request.
- **`POST /tickets` returns 500** -> query Cloud Logging for `jsonPayload.event="model_endpoint_error"` on `ticket-backend-api`. Common causes: model endpoint cold-start timeout, expired ID-token audience mismatch on the Google-signed token to the model, DB write failure.
- **DB connections failing** (`FATAL: password authentication failed for user "app_user"`) -> the `ticket-assistant-db-app-password` secret and the actual Postgres user password have drifted. Re-run the rotation steps above, or re-run `backend/db/apply_schema.sh` which normalizes the `app_user` password to match the secret.
- **Cloud SQL instance status anything other than `RUNNABLE`**:
  ```bash
  gcloud sql instances describe ticket-assistant-db --project=msds-603-victors-demons
  ```
  If stopped, start it:
  ```bash
  gcloud sql instances patch ticket-assistant-db \
    --activation-policy=ALWAYS --project=msds-603-victors-demons
  ```
- **Roll back a bad Cloud Run deploy** to the previous revision:
  ```bash
  gcloud run services update-traffic ticket-backend-api \
    --to-revisions=<previous-revision-name>=100 \
    --region=us-central1 --project=msds-603-victors-demons
  ```
- **Roll back a bad Firebase Hosting deploy.** Firebase Hosting keeps release history:
  ```bash
  firebase hosting:releases:list --site=msds-603-victors-demons --project=msds-603-victors-demons
  firebase hosting:rollback --site=msds-603-victors-demons --project=msds-603-victors-demons
  ```
