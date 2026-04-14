# DistilBERT Fine-Tuning Plan

## Goal

Fine-tune `distilbert-base-uncased` on the synthetic ticket dataset to predict `priority` (low / medium / high / urgent) from `ticket_text`. Training runs on **Vertex AI Custom Training** with a GPU, tracked via **Vertex AI Experiments**, and the final model is registered in **Vertex AI Model Registry**.

The output of this branch is a reproducible cloud training pipeline: `python training/launch.py` submits a Vertex AI Custom Training job that pulls data from GCS, fine-tunes the model on a GPU, logs metrics to Vertex Experiments, saves artifacts to GCS, and registers the model in the Model Registry.

Serving (Cloud Run endpoint, scheduled retraining) is **out of scope** for this branch and will follow in a separate branch.

---

## Scope

**In scope:**
- Local training code that runs end-to-end on CPU against a small subset (smoke test)
- Containerized training job (Dockerfile + Artifact Registry)
- Vertex AI Custom Training submission via the Python SDK
- Vertex AI Experiments integration (metrics, params, artifacts per run)
- Vertex AI Model Registry integration (register each trained model)
- GCS as the data and model artifact store
- Data versioning via explicit version directories (`data/tickets/v1/`, `v2/`, ...)

**Out of scope (future branches):**
- Cloud Run serving endpoint / inference API
- Cloud Scheduler weekly retrain trigger
- Hyperparameter sweeps
- Production monitoring and drift detection

---

## GCP Configuration

| Setting | Value |
|---|---|
| Project | `msds-603-victors-demons` |
| Region | `us-central1` |
| GCS bucket | `msds603-mlflow-artifacts` (reused; bucket name is historical) |
| Training compute | Vertex AI Custom Training, `n1-standard-4` + 1× `NVIDIA_TESLA_T4` |
| Container registry | Artifact Registry (repo to be created in `us-central1`) |
| Experiment tracking | Vertex AI Experiments |
| Model registry | Vertex AI Model Registry |

Estimated cost per training run: ~$0.10 (T4 at ~$0.35/hr × ~10–15 min).

---

## GCS Layout

```
gs://msds603-mlflow-artifacts/
├── data/
│   └── tickets/
│       └── v1/
│           └── tickets.csv              # uploaded once, manually
└── models/
    └── distilbert-priority/
        └── runs/
            └── {run_id}/                 # one dir per training run
                ├── model.safetensors
                ├── tokenizer/
                ├── config.json
                ├── metrics.json
                └── training_args.json
```

The Model Registry holds versioned pointers to these artifact dirs — the `runs/` directory is the raw store, the Model Registry is the curated index.

---

## Model & Training Configuration

| Parameter | Value |
|---|---|
| Base model | `distilbert-base-uncased` |
| Max sequence length | 256 tokens |
| Learning rate | 2e-5 |
| Batch size | 16 |
| Epochs | 3 |
| Warmup ratio | 0.1 |
| Weight decay | 0.01 |
| Optimizer | AdamW (HF default) |
| Train/val/test split | 80 / 10 / 10, stratified by `priority` |
| Random seed | 42 |

Single fixed config for v1. Hyperparameter sweeps are a deliberate follow-up, not bundled here.

---

## Evaluation

Reported on the held-out 10% test set and logged to the Vertex AI Experiment run:

- Overall accuracy
- Macro F1 (primary metric — classes are imbalanced: ~20 / 35 / 30 / 15)
- Per-class precision, recall, F1
- Confusion matrix (logged as an artifact)
- Sample of misclassified examples (logged as an artifact for error analysis)

Priority is ordinal (`low < medium < high < urgent`) — "off by one" misclassifications matter less than "off by two." We do not bake this into the loss for v1, but the confusion matrix makes it visible.

---

## File Structure

```
training/
├── PLAN.md                      # This file
├── Dockerfile                   # Training container (pushed to Artifact Registry)
├── requirements.txt             # Training-only dependencies
├── launch.py                    # Python SDK launcher: submits the Vertex AI Custom Training job
├── train.py                     # Container entrypoint: runs inside the training job
├── config.py                    # Centralized hyperparameters and GCP config
├── data_loader.py               # Load CSV from GCS, split, tokenize
├── evaluate.py                  # Metrics, confusion matrix, misclassification sampling
├── registry.py                  # Vertex AI Model Registry upload logic
├── tests/
│   ├── test_data_loader.py      # Loading, splitting, tokenization
│   ├── test_train.py            # Smoke-test training loop on tiny data
│   └── test_evaluate.py         # Metrics computation on known inputs
└── artifacts/                   # Local outputs from smoke-test runs (gitignored)
```

---

## Smoke Test Mode

`train.py` accepts a `--smoke-test` flag that:
- Loads only the first 100 rows of the dataset
- Trains for 1 epoch with batch size 4
- Skips Vertex AI Experiments logging and Model Registry registration
- Writes artifacts to `training/artifacts/` (local) instead of GCS

Purpose: catch bugs end-to-end (data loading → training → eval → save) in ~1 minute on CPU, before paying for a cloud GPU run.

---

## Pipeline

### Step 1 — Data loading (`data_loader.py`)
- Read `tickets.csv` from `gs://msds603-mlflow-artifacts/data/tickets/{version}/tickets.csv`
- Stratified 80/10/10 split on `priority`
- Tokenize with `distilbert-base-uncased` tokenizer (truncation, max_length=256, padding='max_length')
- Return HuggingFace `Dataset` objects

### Step 2 — Training (`train.py`)
- Parse CLI args (smoke-test flag, data version, run_id)
- Initialize Vertex AI Experiment run (unless smoke-test)
- Load data, initialize `DistilBertForSequenceClassification` with `num_labels=4`
- Fine-tune with HuggingFace `Trainer` using the config above
- Evaluate on test set, compute metrics, generate confusion matrix and misclassification samples
- Log all metrics and artifacts to the Experiment run
- Save model + tokenizer to `gs://.../models/distilbert-priority/runs/{run_id}/`
- Register the model in Vertex AI Model Registry (unless smoke-test)

### Step 3 — Launching (`launch.py`)
- Resolves run_id (timestamp-based)
- Uses Vertex AI Python SDK (`aiplatform.CustomContainerTrainingJob`)
- Points to the training container in Artifact Registry
- Requests `n1-standard-4` + 1× `NVIDIA_TESLA_T4`
- Streams logs to stdout

---

## Testing Strategy

Each file under `tests/` must pass before moving on.

| Test File | What It Covers |
|---|---|
| `test_data_loader.py` | CSV load produces expected columns; stratified split preserves class ratios; tokenization produces correct shape and respects max_length |
| `test_train.py` | Smoke-test training loop runs end-to-end on a 20-row fixture; model saves correctly; metrics dict has expected keys |
| `test_evaluate.py` | Macro F1 and per-class metrics match hand-computed values on a tiny known input; confusion matrix has correct shape |

Run with: `python -m pytest training/tests/ -v`

---

## Implementation Checkpoints

The agent **must stop and request human review** at each checkpoint before continuing.

### Phase 1 — Local Training Pipeline

1. Implement `config.py`, `data_loader.py`, `train.py`, `evaluate.py`
2. Write and run `tests/test_data_loader.py`, `tests/test_evaluate.py` — all tests must pass
3. Write and run `tests/test_train.py` (smoke test) — must pass on CPU
4. Run `python training/train.py --smoke-test` locally against `synthetic_data/data/tickets.csv`
5. Verify local artifacts in `training/artifacts/` and spot-check eval outputs

- [ ] **CHECKPOINT 1:** User inspects smoke-test outputs — training completes, metrics look sane, artifacts saved. **Stop and wait for human approval.**
- [ ] **COMMIT:** After user confirms Phase 1, commit all Phase 1 files.

### Phase 2 — Cloud Training on GPU

6. Upload `tickets.csv` to `gs://msds603-mlflow-artifacts/data/tickets/v1/tickets.csv` (one-off manual step)
7. Write `Dockerfile` and `requirements.txt` for the training container
8. Create Artifact Registry repo in `us-central1`
9. Build and push training container
10. Implement `launch.py` with Vertex AI SDK
11. Submit first Vertex AI Custom Training job via `python training/launch.py`
12. Verify job completes, metrics are logged to Vertex AI Experiments, model artifacts land in GCS

- [ ] **CHECKPOINT 2:** User inspects Vertex AI Experiments UI — run is logged with metrics, confusion matrix, misclassification samples. GCS has the saved model. **Stop and wait for human approval.**
- [ ] **COMMIT:** After user confirms Phase 2, commit all Phase 2 files.

### Phase 3 — Model Registry & Versioning

13. Implement `registry.py` — registers the trained model in Vertex AI Model Registry with metadata (run_id, data version, metrics summary)
14. Integrate registry call into `train.py`
15. Run a fresh end-to-end cloud training job
16. Verify the model appears in the Vertex AI Model Registry UI with correct metadata

- [ ] **CHECKPOINT 3:** User inspects Model Registry — model is registered, versioned, and metadata is attached correctly. **Stop and wait for human approval.**
- [ ] **COMMIT:** After user confirms Phase 3, commit all Phase 3 files.

---

## Downstream Use

The registered model in Vertex AI Model Registry will be consumed by the next branch (serving), which will deploy it behind a Cloud Run inference endpoint. Once serving is in place, a Cloud Scheduler job can trigger `launch.py` on a weekly cron to retrain, and the new model version can be promoted through the Model Registry to the serving endpoint.

---

## Required IAM Roles

The service account used by the training job needs:

- `roles/storage.objectAdmin` (on the GCS bucket) — read data, write artifacts
- `roles/aiplatform.user` — submit custom jobs, log to Experiments, register models
- `roles/artifactregistry.reader` — pull the training container

No external secrets — everything lives in GCP.
