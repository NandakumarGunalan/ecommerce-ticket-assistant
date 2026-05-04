# Online Endpoint Plan

> **HISTORICAL DESIGN DOCUMENT.** This file is the original build plan.
> Most of what's described here has been implemented. The current system
> may have drifted from this design — for the production-truth view of
> how the code actually works, read the source files referenced inline
> in this doc, or `RUNBOOK.md` at the repo root.
>
> **Status:** the online inference service `distilbert-priority-online` IS
> deployed as a Cloud Run service, IAM-restricted, at
> `https://distilbert-priority-online-48533944424.us-central1.run.app`.

## Goal

Serve the fine-tuned DistilBERT priority classifier (registered as `distilbert-priority` in Vertex AI Model Registry) as a **real-time FastAPI endpoint** running on Cloud Run, reusing the shared `predictor.py` core from the batch branch. One ticket in, one prediction out. Authenticated callers only.

The output of this branch is a deployed Cloud Run Service (`distilbert-priority-online`) behind IAM auth, verified end-to-end from a laptop and ready for the teammate's backend to integrate with when it exists.

**Relationship to `PLAN.md`:** The batch plan scaffolded `main.py` and `schemas.py` as placeholders and deliberately kept the predictor core stateless and list-shaped so this branch is additive. Nothing in the batch deployment changes.

---

## Scope

**In scope:**
- `inference/main.py` — FastAPI app with `POST /predict` and `GET /healthz`
- `inference/schemas.py` — Pydantic request/response models
- Unit tests with FastAPI `TestClient` + mocked predictor
- Local integration test (uvicorn + real Vertex model + curl)
- Dockerfile update: support an online-mode `CMD` override (same image, different entrypoint)
- Cloud Run Service deploy, IAM-restricted, scale-to-zero
- Per-request structured prediction logging (event `online_prediction`)
- Demo-day warm-up playbook (`--min-instances=1` before, revert after)

**Out of scope:**
- Rate limiting, quotas, abuse prevention — belongs in the calling tier (backend / API gateway), not here. See "Trust Boundary" below.
- Batch endpoint on the online service — batch is a separate Cloud Run Job; mixing them reintroduces the pre-split coupling.
- A/B traffic split between model versions
- Prometheus `/metrics`
- Versioned URL paths (`/v1/predict`) — YAGNI; add when we actually break the contract
- Autoscaling tuning beyond a max-instances cap

---

## Architecture

```
   ┌───────────────────────────────────────────┐
   │  Vertex AI Model Registry                 │
   │  distilbert-priority (default version)    │
   └──────────────────┬────────────────────────┘
                      │ cold-start load
                      ▼
   ┌───────────────────────────────────────────┐
   │  Cloud Run Service: distilbert-priority-  │
   │  online  (same image as batch job)        │
   │                                           │
   │  uvicorn inference.main:app               │
   │    POST /predict   → predictor.predict()  │
   │    GET  /healthz   → {"status": "ok"}     │
   │                                           │
   │  --no-allow-unauthenticated               │
   │  --min-instances=0                        │
   │  --max-instances=5                        │
   │  --timeout=30s                            │
   └──────────────────┬────────────────────────┘
                      │
                      ▼
            Cloud Logging
            (event: online_prediction)
```

**Key design decisions:**

1. **Same container image as batch**, different `CMD`. `ENTRYPOINT` stays as the batch module in the Dockerfile; online deployment passes `--command` / `--args` at `gcloud run deploy` time to run uvicorn instead. One image, one set of deps, zero duplicated inference logic.
2. **Single-ticket only.** Real-time framing. The request shape is a single `ticket_text`, not a list. Latency is predictable, contract is narrow, backends that want batch can loop — or we add `/predict:batch` later when there's a demonstrated need.
3. **Trust boundary at Cloud Run IAM.** Authenticated callers (IAM invoker role) are trusted. No app-level rate limiting, API keys, or quota logic. Industry-standard pattern for internal service-to-service: authenticate at the edge, put business-context concerns (rate limits, user quotas, abuse prevention) in the tier that has user identity.
4. **Scale-to-zero by default.** Idle cost $0. Cold start is 3–8s on first request after a ~15 minute idle window (Cloud Run's default instance linger). For demo day, temporarily pin `--min-instances=1` — see "Demo Day Playbook" below.

---

## GCP Configuration

| Setting | Value |
|---|---|
| Project | `msds-603-victors-demons` |
| Region | `us-central1` |
| Service name | `distilbert-priority-online` |
| Image | `us-central1-docker.pkg.dev/msds-603-victors-demons/ml-repo/distilbert-priority-inference:latest` (shared with batch) |
| Compute | `2 vCPU / 4GB` |
| Concurrency | `8` (per-instance) — plenty for single-ticket CPU inference at ~50–200ms |
| Min instances | `0` (scale-to-zero), bumped to `1` for demos |
| Max instances | `5` |
| Request timeout | `30s` |
| Runtime SA | `inference-runner@msds-603-victors-demons.iam.gserviceaccount.com` (same as batch) |
| Auth | `--no-allow-unauthenticated` (Cloud Run IAM; no public invoker) |

Estimated cost: **$0 at idle.** Per-request: ~$0.000005 (200ms of 2-vCPU time). A demo with `--min-instances=1` for one hour is ~$0.02.

---

## Endpoint Contract

### `POST /predict`

**Request:**
```json
{"ticket_text": "my order never arrived and it's been 3 weeks"}
```

**Response (200):**
```json
{
  "predicted_priority": "urgent",
  "confidence": 0.83,
  "all_scores": {"low": 0.02, "medium": 0.05, "high": 0.10, "urgent": 0.83},
  "model_version": "2",
  "model_run_id": "run-20260419-140149",
  "latency_ms": 47
}
```

**Validation:**
- `ticket_text` required, non-empty after strip, max 10,000 chars. Rejects with 400 on violation.
- Tokenizer handles truncation to 512 tokens beyond that — we don't second-guess it at the API layer.

**Errors:**
- `400 Bad Request` — missing/empty/overlong `ticket_text`
- `401 Unauthorized` — no ID token (Cloud Run rejects before hitting the app)
- `500 Internal Server Error` — model load failure, unexpected inference error. Logged with traceback to Cloud Logging.
- `503 Service Unavailable` — during cold start if model isn't loaded yet (rare; `main.py` loads the model at startup, not on first request)

### `GET /healthz`

**Response (200):**
```json
{"status": "ok", "model_version": "2", "model_run_id": "run-20260419-140149"}
```

Used for:
- Pre-demo warm-up pings
- Cloud Run health checks (default; not strictly required but costs nothing)
- Debugging "is the model actually loaded in this instance"

Returns 503 if the model hasn't finished loading yet.

---

## Trust Boundary (Why No Rate Limiting Here)

Industry pattern for internal service-to-service auth: **authenticate at the edge, authorize per-caller, trust inside the boundary.**

- **Authentication** — Cloud Run IAM rejects any request without a valid ID token before it hits our app. We don't implement this; GCP does.
- **Authorization** — `roles/run.invoker` granted to specific caller SAs (the teammate's backend SA, your laptop user for testing). We manage the grant list; GCP enforces.
- **Rate limiting, quotas, abuse** — live in the calling tier. The backend has user identity and business context; it knows what "too many requests from user X" means. This service doesn't, and duplicating the logic here adds latency and a failure mode without catching a real threat.
- **What we *do* own** — input validation (length cap, empty check). That's not rate limiting; it's "don't let a malformed request crash the tokenizer."

If a future threat model changes this — e.g. the endpoint becomes public — revisit. For internal-only, this is correct.

---

## File Structure (additions / changes to existing)

```
inference/
├── ONLINE_PLAN.md            # This file
├── main.py                   # REWRITE: FastAPI app, /predict + /healthz
├── schemas.py                # REWRITE: Pydantic PredictRequest / PredictResponse
├── Dockerfile                # UPDATE: no ENTRYPOINT change needed; CMD overridable at deploy
├── requirements.txt          # UNCHANGED — fastapi/uvicorn/pydantic already pinned for batch's placeholder scaffolding
└── tests/
    └── test_main.py          # NEW: FastAPI TestClient + mocked predictor
```

No changes to `predictor.py`, `model_loader.py`, `db.py`, `batch_predict.py`, `config.py`, `logging_utils.py`.

---

## Request/Response Module (`schemas.py`)

```python
from pydantic import BaseModel, Field, field_validator

class PredictRequest(BaseModel):
    ticket_text: str = Field(..., max_length=10_000)

    @field_validator("ticket_text")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("ticket_text must be non-empty")
        return v

class PredictResponse(BaseModel):
    predicted_priority: str
    confidence: float
    all_scores: dict[str, float]
    model_version: str
    model_run_id: str
    latency_ms: int

class HealthResponse(BaseModel):
    status: str
    model_version: str | None = None
    model_run_id: str | None = None
```

---

## App Module (`main.py`) sketch

```python
import time
from fastapi import FastAPI, HTTPException
from inference import predictor, model_loader
from inference.logging_utils import log_event
from inference.schemas import PredictRequest, PredictResponse, HealthResponse

app = FastAPI()
_model_state: dict = {"loaded": False}

@app.on_event("startup")
def _load_model() -> None:
    model_loader.load()            # populates module-level singletons
    _model_state["loaded"] = True

@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    if not _model_state["loaded"]:
        raise HTTPException(status_code=503, detail="model not loaded")
    info = model_loader.current()
    return HealthResponse(status="ok", model_version=info.version, model_run_id=info.run_id)

@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse:
    if not _model_state["loaded"]:
        raise HTTPException(status_code=503, detail="model not loaded")
    t0 = time.perf_counter()
    result = predictor.predict_batch([req.ticket_text])[0]
    latency_ms = int((time.perf_counter() - t0) * 1000)
    info = model_loader.current()
    log_event({
        "event": "online_prediction",
        "model_version": info.version,
        "model_run_id": info.run_id,
        "input_preview": req.ticket_text[:100],
        "input_length_chars": len(req.ticket_text),
        "predicted_priority": result["predicted_priority"],
        "confidence": result["confidence"],
        "latency_ms": latency_ms,
    })
    return PredictResponse(
        predicted_priority=result["predicted_priority"],
        confidence=result["confidence"],
        all_scores=result["all_scores"],
        model_version=info.version,
        model_run_id=info.run_id,
        latency_ms=latency_ms,
    )
```

Exact naming of `model_loader.current()` / `.load()` TBD — may already exist under different names from Phase 1 of the batch plan; adapt during implementation.

---

## Dockerfile Update

The batch Dockerfile ends with:
```dockerfile
ENTRYPOINT ["python", "-m", "inference.batch_predict"]
```

For online, we override at deploy time via `--command` and `--args`:
```bash
gcloud run deploy distilbert-priority-online \
    --command=uvicorn \
    --args=inference.main:app,--host=0.0.0.0,--port=8080 \
    ...
```

No Dockerfile change required. If this proves awkward in practice, fall back to clearing `ENTRYPOINT` in the Dockerfile and setting `CMD` in both batch and online deploys — cleaner, slightly more explicit. Decide during Phase 2.

---

## Structured Logging

Per-request log shape (same as batch's `prediction` event but with `event: "online_prediction"` so Log Explorer filters cleanly):

```json
{
  "severity": "INFO",
  "event": "online_prediction",
  "model_version": "2",
  "model_run_id": "run-20260419-140149",
  "input_preview": "my order never arrived",
  "input_length_chars": 142,
  "predicted_priority": "urgent",
  "confidence": 0.83,
  "latency_ms": 47
}
```

No `run_id` field (that's a batch concept). No `ticket_id` (the online caller may not have one — we don't force it into the contract).

**Latency SLO:** not committed in this doc. After deploy, measure p50 and p99 over a representative sample. Revisit if p99 > 500ms.

---

## Testing Strategy

Three phases, each a checkpoint. See "Implementation Checkpoints" below.

### Phase 1 — Unit tests (`tests/test_main.py`)

FastAPI `TestClient` + mocked predictor. Mirrors the `test_batch_predict.py` pattern from the batch branch.

| Test | What it covers |
|---|---|
| `test_healthz_ok` | Returns 200 with model version fields when model is loaded |
| `test_healthz_not_loaded` | Returns 503 if `_model_state["loaded"]` is False |
| `test_predict_happy_path` | Valid request → 200, response shape matches `PredictResponse`, fields populated |
| `test_predict_empty_text` | `{"ticket_text": ""}` → 400 |
| `test_predict_whitespace_only` | `{"ticket_text": "   "}` → 400 |
| `test_predict_overlong_text` | 10,001-char string → 400 |
| `test_predict_missing_field` | `{}` → 400 (Pydantic validation) |
| `test_predict_calls_predictor_once` | Mock assertion that predictor is called exactly once with the input text |

No real model load. No GCP calls. Fast.

### Phase 2 — Local integration

- Run uvicorn locally: `uvicorn inference.main:app --host 0.0.0.0 --port 8080`
- Model loads from Vertex Registry at startup (same path as batch — already verified in batch's Phase 1 smoke test)
- Hit `/healthz` and `/predict` with curl from another terminal
- Verify response shape, reasonable latency (expect ~50–200ms/request on CPU after warm), structured logs print to stdout

### Phase 3 — Deployed smoke test

- Cloud Build the image (should be a no-op if batch's image already has the online code — it will, since we're editing `main.py` in the same image)
- Deploy the Cloud Run Service
- Hit it from laptop with an ID token:
  ```bash
  TOKEN=$(gcloud auth print-identity-token)
  URL=$(gcloud run services describe distilbert-priority-online \
      --region=us-central1 --format='value(status.url)')
  curl -H "Authorization: Bearer $TOKEN" "$URL/healthz"
  curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      -d '{"ticket_text":"my order never arrived"}' "$URL/predict"
  ```
- Verify 200 responses, sane predictions, structured logs in Cloud Logging under `event=online_prediction`

---

## Implementation Checkpoints

**Execution mode: autonomous.** The agent runs all three phases end-to-end without human approval gates. Commit after each phase. Only stop and ask the user if something *actually fails* and the agent can't make a sensible call (e.g. Cloud Build fails in a way that isn't obviously transient, IAM grant returns an error that needs human judgment, local smoke test produces nonsensical predictions).

"Checkpoint" below = self-check: verify the phase's exit criteria are met, then move on. If they're not, fix it and re-check before advancing.

### Phase 1 — App code + unit tests

1. Rewrite `inference/main.py` per sketch above
2. Rewrite `inference/schemas.py` per sketch above
3. Write `inference/tests/test_main.py` — all tests pass under `pytest inference/tests/test_main.py -v`
4. Run full existing test suite — nothing regresses

**Self-check:** all tests green, no regressions in batch tests. **Commit** (`feat: online endpoint app code + unit tests`) and proceed.

### Phase 2 — Local integration

5. Run `uvicorn inference.main:app --host 0.0.0.0 --port 8080` locally against the real Vertex model (use `run_in_background` so the agent can curl it from the same session)
6. Hit `/healthz` and `/predict` with curl; verify responses and logs
7. If the deploy-time `--command` override turns out awkward in Phase 3, revisit here: clear `ENTRYPOINT` in the Dockerfile and set `CMD` explicitly in both deploys. Agent's call.

**Self-check:** `/healthz` returns 200 with model version populated, `/predict` returns a well-formed `PredictResponse` with sane priority+confidence, latency is under ~500ms after warm. **Commit** any Phase 2 changes and proceed. If Phase 2 was pure verification with no file changes, skip the commit.

### Phase 3 — Cloud Run Service deploy

8. Build the image via `gcloud builds submit --config=inference/cloudbuild.yaml .` (reuses existing build config; same image serves both batch and online). **Note: this is a long-running command — hand off to the user's terminal per the `use repo .venv / long-running commands` memory conventions, OR run with `run_in_background` and poll for completion.**
9. Deploy:
   ```bash
   gcloud run deploy distilbert-priority-online \
       --image=us-central1-docker.pkg.dev/msds-603-victors-demons/ml-repo/distilbert-priority-inference:latest \
       --region=us-central1 \
       --service-account=inference-runner@msds-603-victors-demons.iam.gserviceaccount.com \
       --no-allow-unauthenticated \
       --command=uvicorn \
       --args=inference.main:app,--host=0.0.0.0,--port=8080 \
       --cpu=2 --memory=4Gi \
       --min-instances=0 --max-instances=5 \
       --concurrency=8 --timeout=30s \
       --project=msds-603-victors-demons
   ```
10. Grant the user account `roles/run.invoker` on this service for laptop testing (pre-authorized by the user in the ONLINE_PLAN conversation):
    ```bash
    gcloud run services add-iam-policy-binding distilbert-priority-online \
        --region=us-central1 \
        --member=user:bgholderbein@dons.usfca.edu \
        --role=roles/run.invoker \
        --project=msds-603-victors-demons
    ```
11. Smoke-test from laptop with ID token (see Phase 3 curl block above). The agent runs these curl commands itself.
12. Verify logs in Cloud Logging via `gcloud logging read`:
    ```bash
    gcloud logging read \
        'resource.type="cloud_run_revision" AND resource.labels.service_name="distilbert-priority-online" AND jsonPayload.event="online_prediction"' \
        --limit=5 --project=msds-603-victors-demons --format=json
    ```
13. Verify IAM enforcement: hit the URL *without* an ID token and confirm 401/403.

**Self-check:** deployed service returns 200 for `/healthz` and `/predict` with a valid ID token, returns 401/403 without one, structured `online_prediction` events land in Cloud Logging. **Commit** any Phase 3 artifacts (new deploy-config files, docs). Open a PR to `feature/inference-endpoint` with a summary of what was built and what was verified.

### When to actually stop and ask the user

- Cloud Build fails twice in a row with a non-transient error
- Deploy succeeds but the service returns 500s and logs show a code bug that needs design judgment (not just a typo fix)
- IAM grant errors in a way that suggests the user's permissions changed
- Predictions look wrong (e.g. always returns the same class, confidence pinned at 1.0) — indicates a model loading or preprocessing bug worth flagging before wasting more deploy cycles

Otherwise: don't interrupt. The user hit go and wants a finished branch.

---

## Required IAM Roles

**Runtime SA** (`inference-runner@...`): already has everything needed from the batch branch:
- `roles/storage.objectAdmin` on `msds603-mlflow-artifacts` (model artifact download)
- `roles/aiplatform.user` (Model Registry read)
- `roles/logging.logWriter` (structured logs)

No new runtime-SA roles for online mode. Cloud SQL roles (`cloudsql.client`, `cloudsql.instanceUser`) are unused on this path but harmless to leave.

**Caller identity:**
- Your user (`user:bgholderbein@dons.usfca.edu`) — `roles/run.invoker` on the Service, for laptop testing
- Teammate's backend SA (when it exists) — `roles/run.invoker` on the Service. They provide the SA email; one `gcloud` command grants it.

**Build SA:** same as batch (`roles/artifactregistry.writer` on `ml-repo`). No change.

---

## Demo Day Playbook

Scale-to-zero means a cold demo risks a 3–8s first-request wait. Mitigation:

**~1 hour before demo:**
```bash
gcloud run services update distilbert-priority-online \
    --region=us-central1 \
    --min-instances=1 \
    --project=msds-603-victors-demons
```

**After demo:**
```bash
gcloud run services update distilbert-priority-online \
    --region=us-central1 \
    --min-instances=0 \
    --project=msds-603-victors-demons
```

Cost of leaving `min-instances=1` for a 1-hour demo window: ~$0.02. Forgetting to revert costs ~$15/month — set a calendar reminder. Alternatively, just hit `/healthz` once a minute before the demo; Cloud Run keeps the warm instance up for ~15 minutes of idle, which typically covers a class presentation.

---

## Teammate Handoff (when their backend exists)

One command once they tell us their backend SA email:

```bash
gcloud run services add-iam-policy-binding distilbert-priority-online \
    --region=us-central1 \
    --member=serviceAccount:<their-backend-sa>@<their-project>.iam.gserviceaccount.com \
    --role=roles/run.invoker \
    --project=msds-603-victors-demons
```

They call the endpoint with a Google-signed ID token on every request. Their code looks roughly like (Python):
```python
import google.auth.transport.requests
import google.oauth2.id_token
import requests

SERVICE_URL = "https://distilbert-priority-online-<hash>-uc.a.run.app"
auth_req = google.auth.transport.requests.Request()
token = google.oauth2.id_token.fetch_id_token(auth_req, SERVICE_URL)
r = requests.post(
    f"{SERVICE_URL}/predict",
    headers={"Authorization": f"Bearer {token}"},
    json={"ticket_text": "..."},
    timeout=10,
)
```

No API keys, no secrets, no shared credentials. GCP workload identity handles it.

---

## Lifecycle

Leave the service deployed indefinitely. Scale-to-zero means idle cost is $0. Only tear down if the project is abandoned:
```bash
gcloud run services delete distilbert-priority-online --region=us-central1 --project=msds-603-victors-demons
```

---

## Open Questions (for follow-up)

- **Batch route on the online service?** If the teammate's backend shows up and wants to score 20 tickets in one round-trip, we'd add `POST /predict:batch` taking a list. Trivial addition; don't build until asked.
- **Model version pinning per-request?** Current design uses whatever version the container loaded at startup. If we ever want "score this with v3 specifically" for A/B, add an optional `model_version` field to `PredictRequest` and thread it through. Not needed now.
- **Streaming / long responses?** Irrelevant for a classifier. Noted only to close the door.
