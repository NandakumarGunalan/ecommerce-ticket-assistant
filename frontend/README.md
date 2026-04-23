# Ticket Triage Console

Frontend for the ecommerce ticket assistant. Talks to the backend API
(`ticket-backend-api`) which in turn calls the ML model endpoint and persists
tickets + feedback to Cloud SQL.

## What It Does

- Agent pastes a customer ticket.
- Frontend calls `POST /tickets` on the backend, which predicts priority **and**
  persists the ticket + prediction.
- Displays the predicted priority, confidence, model version, and ticket id.
- Offers thumbs up / thumbs down feedback buttons that call `POST /feedback`.
- Lists recent tickets via `GET /tickets?limit=50`, pre-sorted by the backend
  (urgent > high > medium > low, then newest first).
- Checks endpoint health via `GET /health` on page load.
- Optional mock mode for offline UI work.

## Run Locally

From the project root:

```bash
python3 -m http.server 5173 --directory frontend
```

Open:

```text
http://127.0.0.1:5173
```

Force mock mode (no backend required):

```text
http://127.0.0.1:5173?mock=true
```

## Configure The Backend URL

Edit `frontend/config.js`:

```js
window.TICKET_CONSOLE_CONFIG = {
  API_BASE_URL: "https://ticket-backend-api-48533944424.us-central1.run.app",
  USE_MOCK_API: false,
};
```

The default points at the deployed Cloud Run backend.

## Backend Contract

All routes are served by `ticket-backend-api`. Responses are JSON.

### `GET /health`

```json
{
  "status": "ok",
  "model_version": "2",
  "model_run_id": "run-20260419-140149"
}
```

The UI surfaces `model_version` in the status pill.

> Note: the backend uses `/health` (not `/healthz`) because the Cloud Run GFE
> intercepts `/healthz`.

### `POST /tickets`

Request:

```json
{ "ticket_text": "My order never arrived" }
```

Response:

```json
{
  "ticket_id": "uuid",
  "prediction_id": "uuid",
  "predicted_priority": "medium",
  "confidence": 0.72,
  "all_scores": { "low": 0.04, "medium": 0.72, "high": 0.22, "urgent": 0.02 },
  "model_version": "2",
  "model_run_id": "run-20260419-140149",
  "latency_ms": 38,
  "created_at": "2026-04-23T19:40:16Z"
}
```

This endpoint both predicts and persists. The frontend no longer uses a
stateless `/predict` route.

### `GET /tickets?limit=50`

Returns an array of the response shape above, sorted by priority rank
(urgent > high > medium > low, unknown last), then `created_at DESC`. The
frontend does not re-sort client-side.

### `POST /feedback`

Request:

```json
{ "prediction_id": "uuid", "verdict": "thumbs_up" }
```

`verdict` is one of `thumbs_up`, `thumbs_down`. Optional `note` string allowed.

Response:

```json
{ "feedback_id": "uuid", "created_at": "2026-04-23T19:40:16Z" }
```

### Priority display

Values like `high_priority` are accepted and displayed as `High`. Unknown
values display as `Unknown`.

## CORS

The backend currently allows `*` for CORS, so the frontend can be served from
any origin (including the deployed Cloud Run frontend URL and local dev).

## Tickets View

Backed by `GET /tickets?limit=50`. The view refreshes automatically after each
successful prediction and can be refreshed manually via the Refresh button.
There is no `localStorage` state for the tickets list anymore.

Each row exposes thumbs-up / thumbs-down buttons that call `POST /feedback`
using the row's `prediction_id`.

## Mock Mode

Mock mode is useful when the backend is unavailable or you're working offline.

Force mock mode:

```text
http://127.0.0.1:5173?mock=true
```

Or edit `frontend/config.js`:

```js
window.TICKET_CONSOLE_CONFIG = {
  API_BASE_URL: "https://ticket-backend-api-48533944424.us-central1.run.app",
  USE_MOCK_API: true,
};
```

Mock responses mirror the real contract:
- `/health` → `{status: "ok", model_version: "mock", model_run_id: "mock"}`
- `POST /tickets` → fake ticket with generated UUIDs, `predicted_priority: "medium"`
- `GET /tickets` → seeded rows
- `POST /feedback` → fake `{feedback_id, created_at}`

## Tests

Contract-match test:

```bash
.venv/bin/python frontend/tests/test_contract.py
```

This scans `app.js` and asserts it speaks the documented contract (correct
routes, correct request bodies, no stale fields like `category` or
`localStorage` usage for the tickets view).

## Manual Smoke Checklist

1. Open deployed frontend URL.
2. Status pill shows "Online, model <version>".
3. Paste a ticket and click **Classify ticket** → a prediction card appears
   with priority, confidence, model version, and short ticket id.
4. Click thumbs up → buttons collapse and "Thanks for the feedback" appears.
5. Click the **Tickets** tab → the new ticket appears at the top (or where its
   priority rank sorts it).
6. Click **Refresh** in Tickets view → list reloads without errors.

## Deploying The Frontend

Static container built with `frontend/Dockerfile` (Python `http.server`),
deployed to Cloud Run as service `ticket-frontend`.

```bash
gcloud builds submit --config=frontend/cloudbuild.yaml \
  --project=msds-603-victors-demons .

gcloud run deploy ticket-frontend \
  --image=us-central1-docker.pkg.dev/msds-603-victors-demons/ml-repo/ticket-frontend:latest \
  --region=us-central1 \
  --allow-unauthenticated \
  --cpu=1 --memory=256Mi \
  --min-instances=0 --max-instances=3 \
  --concurrency=50 --timeout=30s \
  --project=msds-603-victors-demons
```
