# Inference Plan

## Goal

Serve the fine-tuned DistilBERT priority classifier (registered as `distilbert-priority` in Vertex AI Model Registry) as a **batch scoring job** running on Cloud Run Jobs, with the architecture designed so that adding an **online FastAPI endpoint** on Cloud Run is a small follow-up, not a rewrite.

The output of this branch is a reproducible batch inference pipeline: `gcloud run jobs execute distilbert-priority-batch --args=...` pulls the current default model version from Model Registry (or a pinned version), queries the application database for unscored tickets, writes predictions back into a dedicated `predictions` table in the same database, and emits structured prediction logs to Cloud Logging for downstream retraining / drift analysis.

**Design note — why DB-direct, not file-based interchange:** for class-project scale (thousands of tickets/day, one producer, one consumer) a file-based interchange layer (CSV or Parquet on GCS between extract → inference → publish) is over-engineered. It adds three moving parts to isolate a concern we don't actually have — multiple downstream consumers. We go DB-direct: the inference container reads from the tickets table and writes to the predictions table, all in one job. The file-based pattern becomes the right call around 10M+ rows/day or when a second consumer (a dashboard, an analyst notebook, a second ML service) shows up; at that point we'd switch to Parquet-on-GCS as the interchange, not CSV. For now, keep it simple.

---

## Scope

**In scope:**
- Shared predictor core (`inference/predictor.py`) that both batch and online modes will use
- Vertex AI Model Registry loader that resolves the model by alias (latest default version, overridable via `MODEL_VERSION` env var)
- Batch CLI (`inference/batch_predict.py`): DB query → inference → DB insert into predictions table + run summary
- DB client abstraction (`inference/db.py`) — thin wrapper over whichever DB the teammate picks, isolates SQL from inference logic
- CPU-only serving container (`inference/Dockerfile`), built via Cloud Build
- Cloud Build config (`inference/cloudbuild.yaml`) mirroring the training build
- Unit tests for predictor core and DB roundtrip (with mocked DB client)
- Structured prediction logging (JSON to Cloud Logging) for both modes
- Cloud Run Job deploy config (IAM-restricted, no public invoker)
- **Predictions table schema contract** — we declare it, teammate creates it via migration

**Out of scope (future work):**
- Online FastAPI endpoint on Cloud Run — scaffolded via shared predictor, wired in a follow-up
- Cloud Scheduler cron for daily batch runs — on-demand only for v1
- Prometheus `/metrics` endpoint
- A/B testing / traffic split between model versions
- Drift detection and retraining triggers
- Parquet-on-GCS interchange layer (not needed until we have a second consumer or 10M+ rows/day)

---

## Architecture

```
   ┌─────────────────────────────────────────┐
   │        Vertex AI Model Registry         │
   │      distilbert-priority (parent)       │
   │   v1 ──── v2 ──── v3 (default) ◄── alias│
   └────────────────────┬────────────────────┘
                        │  resolves to
                        ▼
    gs://.../runs/{run_id}/model/  (HF artifacts,
                        │           downloaded on cold start)
                        ▼
   ┌──────────────────────────────────────────┐
   │  inference/ container (CPU)              │
   │  ┌────────────────────────────────────┐  │
   │  │        predictor.py (shared)       │  │
   │  └───┬────────────────────────────────┘  │
   │      │                                   │
   │  ┌───┴──────────────┐   ┌─────────────┐  │
   │  │ batch_predict.py │   │   main.py   │  │
   │  │   (v1 scope)     │   │  (future)   │  │
   │  └───┬──────────────┘   └──────┬──────┘  │
   │      │                         │         │
   │  ┌───┴──────┐                  │         │
   │  │  db.py   │                  │         │
   │  └───┬──────┘                  │         │
   └──────┼─────────────────────────┼─────────┘
          │                         │
          │  reads unscored         │  future:
          │  tickets, writes        │  online POST /predict
          │  predictions            │
          ▼                         ▼
   ┌──────────────────┐    (teammate's backend /
   │  Application DB  │     service-to-service caller)
   │ ┌──────────────┐ │
   │ │   tickets    │ │ ◄──── backend writes here
   │ └──────────────┘ │
   │ ┌──────────────┐ │
   │ │ predictions  │ │ ◄──── inference writes here
   │ └──────────────┘ │
   └──────────────────┘
          │
          ▼
   Cloud Logging (structured prediction events)
```

**Key design decisions:**

1. **Same container image, two entrypoints.** Batch runs as a Cloud Run Job (`python -m inference.batch_predict ...`). Online runs as a Cloud Run Service (`uvicorn inference.main:app ...`). Both import `predictor.py`. One container to build, one set of deps, zero duplicated inference logic.
2. **DB-direct I/O.** The batch job reads from the `tickets` table and writes to a separate `predictions` table — no GCS interchange files. Simpler, fewer moving parts, fine at our scale.
3. **Isolated DB client.** All SQL lives in `db.py`. If the teammate picks Cloud SQL Postgres today and BigQuery tomorrow, only `db.py` changes — `predictor.py` and `batch_predict.py` don't.
4. **Ownership split.** This branch owns the inference container and the `predictions` table *schema*. The teammate's backend owns the DB itself, the `tickets` table, and the migration that creates `predictions`.

---

## GCP Configuration

| Setting | Value |
|---|---|
| Project | `msds-603-victors-demons` |
| Region | `us-central1` |
| GCS bucket | `msds603-mlflow-artifacts` (reused; historical name) |
| Serving compute | Cloud Run Job, `2 vCPU / 4GB`, max concurrency 1 |
| Container registry | `us-central1-docker.pkg.dev/msds-603-victors-demons/ml-repo/distilbert-priority-inference` |
| Model Registry | `distilbert-priority` (display name) |
| Auth | Cloud Run IAM (`roles/run.invoker`) — no public invoker |

Estimated cost per batch run: **~$0.01** (a few minutes of 2-vCPU at $0.000024/vCPU-sec for a few-thousand-row day). At idle: **$0.00** (Cloud Run Jobs don't bill between executions).

---

## GCS Layout

```
gs://msds603-mlflow-artifacts/
├── data/tickets/v1/tickets.csv          # training data (existing)
└── models/distilbert-priority/runs/...  # training artifacts (existing)
```

No new GCS paths for inference. The batch job reads from the DB and writes to the DB. The only GCS access from the inference container is reading model artifacts on cold start (already covered by `roles/storage.objectAdmin` on the bucket).

Run-level summary JSON (counts, runtime, class distribution) is logged to Cloud Logging as one structured event at end-of-run, not written to GCS. If we later decide we want durable per-run audit files, we can revisit — keeping it in logs is simpler and easier to query via Log Explorer.

---

## File Structure

```
inference/
├── PLAN.md                    # This file
├── Dockerfile                 # CPU inference container (pushed to Artifact Registry)
├── cloudbuild.yaml            # Cloud Build config for inference image
├── requirements.txt           # Inference-only deps (no datasets/accelerate/sklearn)
├── config.py                  # Centralized config (GCP settings, model display name, DB connection)
├── model_loader.py            # Resolve Vertex Registry → download GCS artifacts → load HF model
├── predictor.py               # Core prediction logic: tokenize, forward pass, softmax, labels
├── db.py                      # DB client: read unscored tickets, insert predictions
├── batch_predict.py           # Batch CLI: DB read → score → DB write + run summary log
├── main.py                    # (placeholder) FastAPI app for future online mode
├── schemas.py                 # (placeholder) Pydantic request/response models for online mode
├── logging_utils.py           # Structured JSON logger for Cloud Logging
└── tests/
    ├── __init__.py
    ├── conftest.py            # Shared fixtures: tiny mock model, mock DB client
    ├── test_predictor.py      # Unit tests for predictor core (mock model)
    ├── test_batch_predict.py  # Unit tests for DB read → score → DB write flow (mocked DB + model)
    ├── test_db.py             # Unit tests for DB client: query shape, upsert semantics, idempotency
    └── test_model_loader.py   # Unit tests for version resolution (mock aiplatform.Model.list)
```

**Reused from the existing placeholder** (after rewrite): `main.py`, `schemas.py`, `config.py`, `__init__.py`, `model_loader.py`, `predictor.py` — all gutted and rewritten against Vertex Registry + HF transformers, not MLflow. FastAPI scaffolding in `main.py` kept as a placeholder so the online-mode addition later is additive, not structural.

---

## Model Loading Strategy

`model_loader.py` resolves the model in this order:

1. If `MODEL_VERSION` env var is set (e.g. `"2"`): load that specific version directly by resource name or alias.
2. Else: `aiplatform.Model.list(filter='display_name="distilbert-priority"', order_by="create_time desc")[0]` → use the most recent version whose `is_default_version=True`.
3. Read that version's `artifact_uri` (a `gs://.../runs/{run_id}/model/` path written by `training/registry.py`).
4. Download the whole dir to a local temp path.
5. `AutoModelForSequenceClassification.from_pretrained(local_path)` + `AutoTokenizer.from_pretrained(local_path)`.
6. Cache the loaded model + tokenizer + `model_version` + `model_run_id` as module-level singletons for the lifetime of the process.

**Why this matters:** a future `register_model` call that sets a new default version → next container cold start picks it up automatically, no redeploy. `MODEL_VERSION` env var acts as the rollback escape hatch.

**Cold start cost:** ~3-8s (GCS download of ~270MB + HF load). Acceptable for batch (runs once per invocation anyway); acceptable for online with scale-to-zero (first request of an idle period pays the cost).

---

## Prediction Request/Response Shape (online mode, for reference)

These are scaffolded now even though online isn't deployed yet, so that the predictor core's return shape matches future API contracts.

**Input (single):**
```json
{"ticket_text": "my order never arrived and it's been 3 weeks"}
```

**Input (batch):**
```json
{"tickets": [
  {"id": "T-001", "ticket_text": "my order never arrived"},
  {"id": "T-002", "ticket_text": "thanks for fast shipping"}
]}
```

**Output (always list-shaped internally, unwrapped for single input):**
```json
{
  "predictions": [
    {
      "id": "T-001",
      "predicted_priority": "urgent",
      "confidence": 0.83,
      "all_scores": {"low": 0.02, "medium": 0.05, "high": 0.10, "urgent": 0.83}
    }
  ],
  "model_version": "2",
  "model_run_id": "run-20260419-140149",
  "latency_ms": 47
}
```

`all_scores` is included because decision-threshold tuning (see `.claude/cross_entropy_loss.md`) requires the full distribution, not just the argmax. It's ~2 lines of code and essential for serious downstream use.

---

## Batch CLI Spec

```
python -m inference.batch_predict \
    [--since 2026-04-19]          # optional: score tickets created_at >= this date
    [--ticket-ids T-001,T-002]    # optional: score a specific set (comma-separated)
    [--model-version 2]           # optional: pin to a specific registered version
    [--run-id my-custom-id]       # optional: override auto-generated timestamp run id
    [--batch-size 32]             # inference batch size (not DB fetch size)
    [--dry-run]                   # fetch and score, but do not write to DB
```

**Default behavior (no flags):** score all tickets that do not yet have a row in `predictions` for the current model version. In other words, "catch up to the current default model." A daily cron invocation would typically use this default.

**Selection modes (mutually exclusive):**
- Default: `ticket_id NOT IN (SELECT ticket_id FROM predictions WHERE model_version = <current>)`
- `--since YYYY-MM-DD`: only tickets with `created_at >= YYYY-MM-DD`, minus those already scored by current model
- `--ticket-ids`: exact set, re-scores even if already present (useful for backfilling after bugs)

**Run summary (logged to Cloud Logging, one event per run):**
```json
{
  "severity": "INFO",
  "event": "batch_run_summary",
  "run_id": "pred-20260420-030000",
  "model_version": "2",
  "model_run_id": "run-20260419-140149",
  "selection_mode": "default",
  "row_count": 1247,
  "row_count_scored": 1243,
  "row_count_skipped": 4,
  "class_distribution": {"low": 182, "medium": 432, "high": 498, "urgent": 131},
  "avg_confidence": 0.72,
  "runtime_sec": 47.3,
  "started_at": "2026-04-20T03:00:00Z",
  "finished_at": "2026-04-20T03:00:47Z"
}
```

**Failure modes to handle:**
- Empty selection (nothing to score) → log an info event, exit 0, no DB writes
- Malformed row (null / empty `ticket_text`) → log a warning with `ticket_id`, skip row, increment `row_count_skipped`
- DB read failure → exit non-zero, no writes attempted
- DB write failure mid-batch → each inference batch inserted in its own transaction; a failure in batch N leaves batches 1..N-1 committed. Re-running with default selection picks up only the unfinished tickets (idempotent by design).
- Vertex Registry or GCS failure during model load → exit non-zero before any DB read, nothing committed

---

## Predictions Table Contract

This is the schema contract we hand to the teammate. They own the migration that creates this table; we own the writes.

**Proposed schema (Postgres DDL — adapt for whatever DB the teammate picks):**

```sql
CREATE TABLE predictions (
    ticket_id         TEXT        NOT NULL,
    model_version     TEXT        NOT NULL,  -- Vertex Registry version id, e.g. "2"
    model_run_id      TEXT        NOT NULL,  -- training run id, e.g. "run-20260419-140149"
    predicted_priority TEXT       NOT NULL,  -- one of: low, medium, high, urgent
    confidence        REAL        NOT NULL,  -- max softmax probability, 0..1
    all_scores        JSONB       NOT NULL,  -- {"low": 0.02, "medium": 0.05, ...}
    predicted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_run_id      TEXT        NOT NULL,  -- links rows to a batch invocation
    PRIMARY KEY (ticket_id, model_version),
    FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id)
);

CREATE INDEX predictions_model_version_idx ON predictions(model_version);
CREATE INDEX predictions_batch_run_id_idx ON predictions(batch_run_id);
```

**Write semantics:**
- `INSERT ... ON CONFLICT (ticket_id, model_version) DO NOTHING` by default. Same ticket scored by same model version a second time is a no-op.
- `--ticket-ids` explicit mode uses `ON CONFLICT DO UPDATE` to allow deliberate re-scores.
- Each inference batch (`--batch-size`) is one transaction. Partial progress on failure is fine — default selection mode will pick up the rest on retry.

**Reads (out of scope here, but for the teammate):**
- The application backend joins `tickets LEFT JOIN predictions ON tickets.ticket_id = predictions.ticket_id AND predictions.model_version = <current default>` to show the current score.
- If they want "score as of a specific model version," they query `WHERE model_version = 'X'`.
- Historical drift analysis: `SELECT model_version, AVG(confidence), COUNT(*) FROM predictions GROUP BY model_version` gives version-over-version comparisons for free.

**DB choice: Cloud SQL for PostgreSQL.** Decided. The schema above uses Postgres-native types (`JSONB`, `TIMESTAMPTZ`) and Postgres upsert semantics (`INSERT ... ON CONFLICT`). `db.py` will use `pg8000` (pure-Python Postgres driver — no native build dependencies, smaller container) plus `cloud-sql-python-connector` for IAM-based auth from the Cloud Run Job to Cloud SQL. No passwords, no proxy sidecar, no VPC plumbing.

---

## Online Mode (Future)

`main.py` and `schemas.py` scaffold a FastAPI app using the same `predictor.py`. Adding online mode later is:

1. Fill in `main.py`: `@app.post("/predict")` → `predictor.predict_batch(texts) → response`
2. Add `serve` entrypoint variant to Dockerfile: `CMD ["uvicorn", "inference.main:app", "--host", "0.0.0.0", "--port", "8080"]`
3. `gcloud run deploy distilbert-priority-online --image=... --no-allow-unauthenticated` (Cloud Run Service, not Job)
4. Grant `roles/run.invoker` to the backend's service account

**Deliberately minimal surface area for online mode in v1**: scaffolding files exist with `TODO` comments pointing to how to finish them. The core prediction code is already written and tested. Actual deployment happens in a follow-up branch.

---

## Auth

**Cloud Run IAM (`--no-allow-unauthenticated`).** Both the batch Job and the future online Service.

- The batch Job's own runtime service account (`inference-runner@msds-603-victors-demons.iam.gserviceaccount.com`, to be created) needs:
  - `roles/storage.objectAdmin` on the bucket (read input, write output)
  - `roles/aiplatform.user` (read Model Registry)
  - `roles/logging.logWriter` (structured logs)
- The online Service (when added) uses the same service account for its own GCP calls.
- To *invoke* either from another GCP service (your teammate's backend), that service's SA needs `roles/run.invoker` on the specific Job / Service.
- The `roles/run.invoker` role explicitly does NOT propagate to the runtime SA — the caller's identity is separate from the workload's identity. Good security hygiene, native to Cloud Run.

**What this prevents:** anyone outside our GCP project cannot invoke the batch or online endpoint, even if they guess the URL. Misuse surface = internal to the project.

---

## Structured Prediction Logging

Every batch run writes one JSON log per prediction (or batched in groups of 100 to avoid log spam) to Cloud Logging with this shape:

```json
{
  "severity": "INFO",
  "event": "prediction",
  "run_id": "pred-20260420-030000",
  "model_version": "2",
  "model_run_id": "run-20260419-140149",
  "ticket_id": "T-001",
  "input_preview": "my order never arrived",
  "input_length_chars": 142,
  "predicted_priority": "urgent",
  "confidence": 0.83,
  "latency_ms": 47
}
```

`input_preview` is truncated to first 100 chars to avoid logging PII at scale. This log format enables a BigQuery sink later for retraining data collection and drift analysis without needing to add that infrastructure now.

---

## Testing Strategy

Unit tests only. Each file under `tests/` must pass before deploying.

| Test File | What It Covers |
|---|---|
| `test_predictor.py` | Given a mock HF model + tokenizer, predictor returns correct shape (list of dicts), correct label ordering, softmax probabilities sum to ~1, confidence matches argmax. Edge cases: empty text, text longer than max_length (should truncate). |
| `test_batch_predict.py` | End-to-end with mocked predictor and mocked `db.py`: fetched rows → scored → correct insert calls issued. Summary counts match. Edge cases: empty selection, malformed row, partial batch failure (mid-batch exception leaves earlier batches committed, re-run picks up remainder). |
| `test_db.py` | Unit tests for the DB client against an in-memory sqlite test harness (schema-compatible subset). Verifies: default selection excludes already-scored tickets for the current model version; `--since` filter applies; upsert on conflict behaves correctly; `--ticket-ids` override re-scores. Sqlite is not the production DB but is close enough to catch logic bugs without provisioning Cloud SQL for tests. |
| `test_model_loader.py` | With mocked `aiplatform.Model.list`, correct version selection (latest default, or `MODEL_VERSION` override). Does not actually download model. |

Run with: `python -m pytest inference/tests/ -v`

**Reference**: the existing `test/fastapi-endpoint-unit-tests` branch (remote-only, not merged) has FastAPI `TestClient` fixtures we may mine for online-mode tests later. Not used in v1 batch scope.

---

## Dependencies

`inference/requirements.txt`:

```
torch==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu
transformers==4.46.3
google-cloud-aiplatform==1.71.1
google-cloud-storage==2.18.2
google-cloud-logging==3.11.0
pandas==2.2.3
fastapi==0.115.4
uvicorn[standard]==0.32.0
pydantic==2.9.2
```

Deliberately omitted (vs. training): `datasets`, `accelerate`, `scikit-learn`, `gcsfs`, CUDA packages. Image target: ~1.8GB (vs training's ~7GB).

---

## Dockerfile Sketch

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY inference/requirements.txt /app/inference/requirements.txt
RUN pip install --no-cache-dir -r /app/inference/requirements.txt

COPY inference /app/inference

ENV PYTHONPATH=/app

# Default entrypoint = batch mode. Cloud Run Service deployment later
# overrides CMD to run uvicorn.
ENTRYPOINT ["python", "-m", "inference.batch_predict"]
```

Built via `gcloud builds submit --config=inference/cloudbuild.yaml .` — same pattern as training, no Mac/QEMU issues.

---

## Implementation Checkpoints

The agent must stop and request human review at each checkpoint.

### Phase 1 — Shared Core + Local Smoke Test

1. Write `config.py`, `logging_utils.py`, `model_loader.py`, `predictor.py`
2. Write `tests/test_predictor.py` and `tests/test_model_loader.py` — all must pass
3. Run a local CLI smoke test that loads the real Vertex model and predicts on a tiny hand-crafted list of 3 tickets (no CSV, just `python -c "..."`) — verifies Vertex→GCS→HF path works locally

- [x] **CHECKPOINT 1:** User inspects: model loads correctly, predictions look sane, tests pass. **Stop and wait for human approval.**
- [x] **COMMIT:** After approval, commit Phase 1 files.

### Phase 2 — Batch CLI + Container

4. Write `batch_predict.py` and `tests/test_batch_predict.py` — tests pass
5. Local run: `python -m inference.batch_predict --input <local csv> --output-dir <local dir>` against a tiny CSV
6. Write `Dockerfile`, `requirements.txt`, `cloudbuild.yaml`
7. Build via `gcloud builds submit --config=inference/cloudbuild.yaml .`
8. Test the container locally: `docker run --rm -v $PWD/tmp:/tmp <image> --input /tmp/tiny.csv --output-dir /tmp/out`

- [x] **CHECKPOINT 2:** User inspects: local CSV roundtrip works, container builds successfully, container runs locally. **Stop and wait for human approval.**
- [x] **COMMIT:** After approval, commit Phase 2 files.

### Phase 3 — Cloud Run Job Deploy

9. Create the `inference-runner` service account with required roles (see Required IAM Roles section)
10. Deploy the Cloud Run Job: `gcloud run jobs deploy distilbert-priority-batch --image=... --service-account=... --no-allow-unauthenticated`, with placeholder Cloud SQL env vars
11. Stand up a throwaway Cloud SQL Postgres instance, seed ~50 tickets from the training CSV, apply the predictions table schema (per "Predictions Table Contract" section)
12. Update Job env vars with real connection details. Invoke: `gcloud run jobs execute distilbert-priority-batch --region=us-central1 --wait`
13. Verify: predictions table populated, `model_version` in inserted rows matches Registry default, structured logs appear in Cloud Logging, class distribution is sane
14. Tear down the throwaway instance; reset Job env vars to placeholders pending the teammate's production Cloud SQL (see "Teammate Handoff" below)

- [x] **CHECKPOINT 3:** User inspects: Job runs successfully on Cloud Run, outputs land in GCS, logs are structured. **Stop and wait for human approval.**
- [x] **COMMIT:** After approval, commit Phase 3 files.

**Phase 3 smoke test result (end-to-end, 2026-04-21):** provisioned a throwaway `db-f1-micro` Cloud SQL Postgres instance, seeded 50 tickets from the training CSV, executed the Cloud Run Job against it. Result: all 50 tickets scored, predictions table populated with correct model version (`2`), class distribution `{low:6, medium:18, high:21, urgent:5}` matching training-set shape, sample confidences 0.56–0.95. IAM auth via `roles/cloudsql.client` **plus** `roles/cloudsql.instanceUser` (the latter is required for IAM DB auth — updated the IAM role list below). Instance torn down after verification. Cloud Run Job env vars reset to placeholders pending teammate's real Cloud SQL provisioning.

---

## Required IAM Roles

**`inference-runner@msds-603-victors-demons.iam.gserviceaccount.com`** (workload identity):
- `roles/storage.objectAdmin` on `msds603-mlflow-artifacts` — read input, write output
- `roles/aiplatform.user` — read Model Registry
- `roles/logging.logWriter` — write structured logs
- `roles/cloudsql.client` — open Cloud SQL connections via the Python connector
- `roles/cloudsql.instanceUser` — required for IAM database authentication (login as an IAM user, not a password user)

**Caller identity (teammate's backend SA, when online mode arrives):**
- `roles/run.invoker` on the specific Cloud Run Job / Service

**Build identity (Cloud Build SA, same as training):**
- `roles/artifactregistry.writer` on `ml-repo`

No external secrets.

---

## Teammate Handoff

The inference pipeline is fully deployed and end-to-end verified as of Phase 3 (see smoke-test note above). What's left to go live in production is the teammate's Cloud SQL instance plus a small wire-up step — no code changes on this branch.

### What's already in place (owned by this branch)

- Cloud Run Job `distilbert-priority-batch` in `us-central1`, image `us-central1-docker.pkg.dev/msds-603-victors-demons/ml-repo/distilbert-priority-inference:latest`
- Runtime service account `inference-runner@msds-603-victors-demons.iam.gserviceaccount.com` with all required roles (see Required IAM Roles)
- Job env vars currently set to placeholders: `CLOUD_SQL_CONNECTION_NAME=PLACEHOLDER_PROJECT:REGION:INSTANCE`, `CLOUD_SQL_DB_NAME=PLACEHOLDER_DB`, `CLOUD_SQL_DB_USER=inference-runner@msds-603-victors-demons.iam`

### What the teammate owns

1. **Provision the production Cloud SQL Postgres instance** with IAM authentication enabled:
   ```bash
   gcloud sql instances create <instance-name> \
       --database-version=POSTGRES_15 \
       --tier=<chosen-tier> \
       --region=us-central1 \
       --database-flags=cloudsql.iam_authentication=on \
       --project=msds-603-victors-demons
   ```
2. **Create the database** (name it whatever fits their backend conventions; they tell us the name back).
3. **Create the `tickets` table** per their backend's needs — our pipeline only requires that it expose `ticket_id TEXT` and `ticket_text TEXT` columns (readable by the inference SA). If the real schema has `subject` + `body` separately, they tell us and we add a one-line SELECT concat in `db.py`.
4. **Create the `predictions` table** exactly as specified in the "Predictions Table Contract" section (Postgres DDL is in the plan). Run the migration once.
5. **Register the inference SA as an IAM DB user and grant table-level access:**
   ```sql
   -- From psql as a superuser on the new instance
   CREATE USER "inference-runner@msds-603-victors-demons.iam" WITH LOGIN;
   GRANT CONNECT ON DATABASE <db-name> TO "inference-runner@msds-603-victors-demons.iam";
   GRANT USAGE ON SCHEMA public TO "inference-runner@msds-603-victors-demons.iam";
   GRANT SELECT ON tickets TO "inference-runner@msds-603-victors-demons.iam";
   GRANT SELECT, INSERT, UPDATE ON predictions TO "inference-runner@msds-603-victors-demons.iam";
   ```
   Equivalent gcloud step for the IAM user itself (outside psql):
   ```bash
   gcloud sql users create "inference-runner@msds-603-victors-demons.iam" \
       --instance=<instance-name> \
       --type=CLOUD_IAM_SERVICE_ACCOUNT
   ```
6. **Report back** with the connection name (format `msds-603-victors-demons:us-central1:<instance-name>`) and the database name.

### What we do once teammate reports back

One command wires the job to production:

```bash
gcloud run jobs update distilbert-priority-batch \
    --region=us-central1 \
    --update-env-vars="CLOUD_SQL_CONNECTION_NAME=<their-conn-name>,CLOUD_SQL_DB_NAME=<their-db-name>,CLOUD_SQL_DB_USER=inference-runner@msds-603-victors-demons.iam" \
    --project=msds-603-victors-demons
```

Then execute once as a production smoke test:

```bash
gcloud run jobs execute distilbert-priority-batch --region=us-central1 --wait
```

Expected: the job queries their tickets table for anything not yet scored under the current default model version, writes predictions rows, exits 0. The Cloud Logging event `batch_run_summary` reports counts and class distribution.

After that succeeds, Cloud Scheduler wiring (see next section) is ~5 lines of gcloud.

### Gotchas we learned during Phase 3 smoke test

- **`roles/cloudsql.client` alone is not enough for IAM DB auth.** The SA also needs `roles/cloudsql.instanceUser`. Without the second role, the `cloud-sql-python-connector` fails fetching an ephemeral cert with `ServerDisconnectedError` — cryptic, and only surfaces at runtime. Both roles are already granted to `inference-runner`; mentioning it here so nobody re-derives it.
- **Cloud SQL first-instance creation on a fresh project is slow.** Enabling the `sqladmin.googleapis.com` API and creating the first instance took ~12 minutes end-to-end during Phase 3. Subsequent creates are faster. Budget accordingly.
- **`cloudsql.iam_authentication=on` must be set at create time** (or via patch, which restarts the instance). Missing this flag is the most common cause of IAM-auth connection failures.

---

## Future: Nightly Automated Classification

The intended steady-state is a nightly Cloud Scheduler cron that invokes this batch job with its default selection mode — "score all unscored tickets under the current default model version."

```
Cloud Scheduler (daily 03:00 UTC = 20:00 PST / 19:00 PDT the previous evening)
           │
           ▼
Cloud Run Job: inference/batch_predict.py  (this branch)
   - queries DB for unscored tickets under current model version
   - runs inference on CPU
   - inserts results into predictions table
   - emits per-prediction + per-run structured logs
```

No extract step, no publisher step, no GCS interchange. The batch job talks to the DB directly on both ends. Daily cron is three commands to wire up once the DB is in place:

```bash
gcloud run jobs deploy distilbert-priority-batch --image=... (already done)
gcloud scheduler jobs create http distilbert-priority-nightly \
    --schedule="0 3 * * *" \
    --uri="<cloud run job exec endpoint>" \
    --oauth-service-account-email=<scheduler SA>
```

Not built in v1 because (a) the DB doesn't exist yet and (b) it's trivial to add once it does.

**Retrain flow (future):** when a new model version is registered with `is_default_version=True`, the next nightly run will see "tickets that don't have a row for the new `model_version`" — which is every ticket — and score all of them. At our scale this is fine (thousands of rows, minutes of CPU). At real scale you'd want a more selective policy — either a `--since` window or an explicit backfill job on a separate schedule. Document as a follow-up when volumes grow.

---

## Open Questions (for follow-up branches)

- **Cloud SQL instance details**: once the teammate provisions the instance, we need the connection name (format: `project:region:instance`), database name, and the inference service account needs `roles/cloudsql.client` granted on the project. Record these in `inference/config.py` (connection name / db name) and as env vars at deploy time.
- **Ticket text column source**: is `ticket_text` a single column in the tickets table, or do we need to concatenate `subject + body` (or similar) in the SELECT? Confirm with teammate before writing the SELECT in `db.py`.
- **Backfill on retrain**: current default behavior scores every historical ticket under a new model version. Fine now, will need a selective policy later. Follow-up branch when we have real volumes.
- **Parquet-on-GCS interchange**: revisit when (a) a second consumer shows up (dashboard, analyst notebook), or (b) row volumes cross ~10M/day. At that point DB-direct becomes the bottleneck and an intermediate columnar file gives us parallelism + reproducibility.
- **Monitoring**: Cloud Run default metrics (invocations, errors, p99 latency) sufficient for v1. `/metrics` Prometheus endpoint is a good interview talking point but adds real ops surface we don't need yet.
