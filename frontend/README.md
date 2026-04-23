# Ticket Triage Console

Frontend for the ecommerce ticket assistant. This is intentionally isolated in
`frontend/` so the UI work can move independently from the ML endpoint,
training pipeline, and synthetic data branches.

## What It Does

- Lets a support agent paste or type a customer ticket.
- Calls the ML endpoint to predict ticket priority.
- Displays priority, confidence, category, and model version.
- Checks endpoint health on page load.
- Saves successfully classified tickets in browser local storage.
- Shows a second `Tickets` view sorted by priority: urgent, high, medium, low, unknown.
- Falls back to mock mode when the endpoint is unavailable, so UI work can continue.

## Run Locally

From the project root:

```bash
python3 -m http.server 5173 --directory frontend
```

Open:

```text
http://127.0.0.1:5173
```

To force mock mode:

```text
http://127.0.0.1:5173?mock=true
```

## Connect To The API

Edit `frontend/config.js`:

```js
window.TICKET_CONSOLE_CONFIG = {
  API_BASE_URL: "http://127.0.0.1:8001",
  USE_MOCK_API: false,
};
```

For local development, the frontend defaults to:

```text
http://127.0.0.1:8001
```

If the backend runs on a different port or a deployed Cloud Run URL, update
`API_BASE_URL`.

## Backend Contract

The frontend only requires two backend routes right now.

### Health Check

```http
GET /health
```

Expected health response:

```json
{
  "status": "ok",
  "model_loaded": true
}
```

Required fields:
- `status`: string
- `model_loaded`: boolean

The UI uses `model_loaded` only to show endpoint status. It does not block
classification if this is `false`.

### Predict Priority

```http
POST /predict
Content-Type: application/json
```

```json
{
  "text": "My order has not arrived yet"
}
```

Required response:

```json
{
  "priority": "medium",
  "category": "ticket_classification",
  "confidence": 0.9,
  "model_version": "v3"
}
```

Required fields:
- `priority`: string, expected values are `low`, `medium`, `high`, `urgent`, or `unknown`
- `category`: string
- `confidence`: number from `0` to `1`
- `model_version`: string

Compatibility notes:
- `priority` values like `high_priority` are accepted and displayed as `High`.
- Missing or unexpected priority values display as `Unknown`.
- The frontend sends exactly one field in the request body: `text`.
- The frontend expects JSON responses and standard HTTP status codes.

## CORS Requirements

If frontend and backend are served from different origins, the backend must allow
the frontend origin.

Local frontend origin:

```text
http://127.0.0.1:5173
```

Recommended FastAPI setup:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
```

For deployment, add the deployed frontend origin to `allow_origins`.

## Tickets View

The current `Tickets` view does not require a backend database yet. After each
successful prediction, the frontend stores the ticket and prediction result in
`localStorage`.

Stored ticket shape:

```json
{
  "id": "browser-generated-id",
  "text": "Customer ticket text",
  "createdAt": 1713830000000,
  "priority": "high",
  "confidence": 0.9,
  "category": "ticket_classification",
  "model_version": "v3"
}
```

Future backend handoff:
- If the team adds persistence later, replace local storage with `POST /tickets`
  after prediction and `GET /tickets?sort=priority` for the tickets page.
- The current UI already expects the saved-ticket shape above, so the backend can
  mirror that structure to minimize frontend changes.

## Mock Mode

Mock mode is useful when the endpoint is unavailable or CORS is not ready.

Force mock mode:

```text
http://127.0.0.1:5173?mock=true
```

Or edit `frontend/config.js`:

```js
window.TICKET_CONSOLE_CONFIG = {
  API_BASE_URL: "http://127.0.0.1:8001",
  USE_MOCK_API: true,
};
```

## Handoff Notes

- Endpoint owner should confirm `GET /health` and `POST /predict` match this contract.
- Frontend owner should update `API_BASE_URL` when a deployed endpoint URL is ready.
- If prediction requests fail, first check CORS, backend port, and whether the endpoint returns JSON.
- The frontend is static HTML/CSS/JS, so no build step is required right now.
