# Ticket Triage Console

Frontend for the ecommerce ticket assistant. Talks to the backend API
(`ticket-backend-api`) which in turn calls the ML model endpoint and persists
tickets + feedback to Cloud SQL.

Starting with Wave 4, the frontend authenticates users with **Firebase
Authentication** (Google Sign-In) and attaches a Firebase ID token as a
`Authorization: Bearer <token>` header on every protected backend call.

## What It Does

- Agent signs in with Google (Firebase Auth).
- Agent pastes a customer ticket.
- Frontend calls `POST /tickets` on the backend, which predicts priority **and**
  persists the ticket + prediction for the authenticated user.
- Displays the predicted priority, confidence, model version, and ticket id.
- Offers thumbs up / thumbs down feedback buttons that call `POST /feedback`.
- Lists recent tickets via `GET /tickets?limit=50`, pre-sorted by the backend
  (urgent > high > medium > low, then newest first), scoped to the signed-in
  user.
- Checks endpoint health via `GET /health` on page load (public, no auth).
- Optional mock mode for offline UI work (bypasses auth entirely).

## Sign-in Flow (GIS + signInWithCredential)

Starting in Wave 5 the frontend uses **Google Identity Services (GIS) +
`signInWithCredential`** — Firebase's recommended "Option 5" pattern
([docs](https://firebase.google.com/docs/auth/web/redirect-best-practices)).
We do **not** use `signInWithPopup` or `signInWithRedirect`.

Why: both of those flows route through a Firebase auth iframe served from
`<project>.firebaseapp.com`. Under Chrome's storage partitioning that iframe
is a third-party frame with no access to its own IndexedDB, so the returned
credential silently never becomes an auth state. Full background in
`.claude/FIREBASE_AUTH_BUG.md` and the `.claude/diagnosis_*.md` files.

The GIS path avoids the iframe entirely:

1. Page loads. `accounts.google.com/gsi/client` is loaded in `<head>`, and the
   Firebase SDK initializes in the module script at the bottom of
   `index.html`. We import only `initializeApp`, `getAuth`,
   `onAuthStateChanged`, `signInWithCredential`, `signOut`, and
   `GoogleAuthProvider` — no `signInWithPopup`, no `signInWithRedirect`,
   no `getRedirectResult`.
2. User clicks "Sign in with Google". `signIn()` calls
   `google.accounts.oauth2.initTokenClient({ client_id, scope: "openid email profile" }).requestAccessToken()`.
   Google owns the popup — not Firebase. No third-party iframe is involved.
3. Google returns an access token to the GIS callback. We exchange it via
   `signInWithCredential(auth, GoogleAuthProvider.credential(null, access_token))`.
   Firebase mints its own session and fires `onAuthStateChanged`.
4. `onAuthStateChanged`:
   - **Signed out** -> show `#auth-gate` with a "Sign in with Google" button.
   - **Signed in** -> hide gate, show the main app and a `#user-bar`.
     Then call `GET /me` to confirm the Firebase ID token is accepted by the
     backend and load the initial `GET /tickets` list.
5. Every fetch to the backend goes through `authedFetch(path, opts)`, which:
   - Fetches a fresh ID token via `auth.currentUser.getIdToken()`.
   - Sets `Authorization: Bearer <token>`.
   - On `401` -> **force-refreshes** the ID token and retries once. Only if
     the retry is also `401` do we surface a visible banner on the auth gate
     and sign the user out. This prevents the silent-loop bug where a
     transient auth hiccup bounced the user back to the sign-in screen with
     no explanation. (Diagnosis C, Fix A.)
   - On `429` -> toasts "Slow down - limit is 50 req/min".
6. Sign-out clicks `signOut(auth)`; the auth listener flips the UI back to
   the gate.

The GIS path uses the **same Google OAuth client id** Firebase configures
for its Google provider: `48533944424-ovpv2i1f9aecvr30jj1ipgg9eo2ho8l1.apps.googleusercontent.com`.
That way the credential Google returns is valid for this Firebase project.

## Firebase config values

The Firebase web config lives in `frontend/config.js`:

```js
FIREBASE_CONFIG: {
  apiKey: "AIzaSyCbzs7DJ7nqD8FELVgOKylABrohPyJg8Zs",
  authDomain: "msds-603-victors-demons.firebaseapp.com",
  projectId: "msds-603-victors-demons",
  appId: "1:48533944424:web:1caab7a98902277a3823dd",
  messagingSenderId: "48533944424",
  storageBucket: "msds-603-victors-demons.firebasestorage.app",
}
```

These are **public** Firebase web config values (not secrets) and are safe to
commit. Security is enforced server-side: the backend verifies the Firebase ID
token on every protected route, and Firebase itself only accepts sign-ins from
authorized domains configured in the Firebase console.

## Run Locally

From the project root:

```bash
python3 -m http.server 5173 --directory frontend
```

Open:

```text
http://127.0.0.1:5173
```

`localhost` is already in the Firebase authorized-domains list, so Google
Sign-In works out of the box for local dev. No extra setup required.

Force mock mode (no backend required, no sign-in required):

```text
http://127.0.0.1:5173?mock=true
```

## Mock Mode

Mock mode is useful when the backend is unavailable or you're working offline.
In mock mode:

- Firebase is **not** initialized.
- A fake user is synthesized: `{ uid: "mock-user", email: "mock@example.com", displayName: "Mock User" }`.
- The sign-in gate is skipped; the app renders directly.
- `authedFetch` skips the token fetch and mock handlers return canned data.

Force mock mode via URL:

```text
http://127.0.0.1:5173?mock=true
```

Or persistently via `frontend/config.js`:

```js
window.TICKET_CONSOLE_CONFIG = {
  API_BASE_URL: "https://ticket-backend-api-48533944424.us-central1.run.app",
  USE_MOCK_API: true,
  FIREBASE_CONFIG: { /* ... */ },
};
```

Mock responses mirror the real contract:
- `/health` -> `{status: "ok", model_version: "mock", model_run_id: "mock"}`
- `POST /tickets` -> fake ticket with generated UUIDs, `predicted_priority: "medium"`
- `GET /tickets` -> seeded rows
- `POST /feedback` -> fake `{feedback_id, created_at}`
- `GET /me` -> fake `{uid, email, display_name}`

## Configure The Backend URL

Edit `frontend/config.js`:

```js
window.TICKET_CONSOLE_CONFIG = {
  API_BASE_URL: "https://ticket-backend-api-48533944424.us-central1.run.app",
  USE_MOCK_API: false,
  FIREBASE_CONFIG: { /* ... */ },
};
```

The default points at the deployed Cloud Run backend.

## Backend Contract

All routes are served by `ticket-backend-api`. Responses are JSON.

**Auth:** All endpoints **except `GET /health`** require a valid Firebase ID
token as `Authorization: Bearer <token>`. The backend returns:

- `401` if the token is missing, expired, or invalid.
- `429` if the caller exceeds **50 requests / minute / user**, with a
  `Retry-After: 60` header.

### `GET /health` (public)

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

### `GET /me` (authed)

```json
{ "uid": "firebase-uid", "email": "you@example.com", "display_name": "Your Name" }
```

Used on sign-in to confirm the token round-trip works and display the user's
name/email.

### `POST /tickets` (authed)

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

This endpoint both predicts and persists. Results are scoped to the
authenticated user.

### `GET /tickets?limit=50` (authed)

Returns an array of the response shape above, sorted by priority rank
(urgent > high > medium > low, unknown last), then `created_at DESC`. Only the
authenticated user's tickets are returned. The frontend does not re-sort
client-side.

### `POST /feedback` (authed)

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
There is no `localStorage` state for the tickets list.

Each row exposes thumbs-up / thumbs-down buttons that call `POST /feedback`
using the row's `prediction_id`.

## Tests

Contract-match test:

```bash
.venv/bin/python frontend/tests/test_contract.py
```

Scans `app.js`, `config.js`, and `index.html` and asserts the frontend speaks
the documented contract: correct routes, correct request bodies, no stale
fields (`category`, `localStorage`), every protected route goes through
`authedFetch`, `FIREBASE_CONFIG` is present, and the Firebase modules are
imported from gstatic.

## Manual Smoke Checklist

1. Open the deployed frontend URL (or local dev server).
2. Sign-in gate appears with a "Sign in with Google" button.
3. Click sign-in -> Google popup -> choose account -> UI flips to main app.
4. `#user-bar` in the header shows your email and a sign-out button.
5. Status pill shows "Online, model <version>".
6. Paste a ticket and click **Classify ticket** -> a prediction card appears
   with priority, confidence, model version, and short ticket id.
7. Click thumbs up -> buttons collapse and "Thanks for the feedback" appears.
8. Click the **Tickets** tab -> your new ticket appears at the top (or where
   its priority rank sorts it). Only your tickets show.
9. Click **Refresh** in Tickets view -> list reloads without errors.
10. Click **Sign out** -> UI returns to the sign-in gate.
11. Append `?mock=true` -> gate is skipped, fake user, seeded tickets.

## Deploying The Frontend

The frontend is deployed to **Firebase Hosting** (site
`msds-603-victors-demons`). `firebase.json` lives in this directory. No
build step — static assets only.

```bash
cd frontend
firebase deploy --only hosting --project=msds-603-victors-demons
```

Live URLs (both point at the same release):

- `https://msds-603-victors-demons.firebaseapp.com`
- `https://msds-603-victors-demons.web.app`

Both are on Firebase Auth's authorized-domains list.

The previous Cloud Run frontend service `ticket-frontend` has been
**deleted**. `frontend/Dockerfile` and `frontend/cloudbuild.yaml` remain
in the repo for historical reference only; do not redeploy them.

To roll back a bad Hosting deploy:

```bash
firebase hosting:releases:list --site=msds-603-victors-demons \
  --project=msds-603-victors-demons
firebase hosting:rollback --site=msds-603-victors-demons \
  --project=msds-603-victors-demons
```
