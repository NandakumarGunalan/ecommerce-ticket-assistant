# Where to find evaluation metrics

This repo trains a `distilbert-priority` classifier (priority = low / medium / high / urgent). Evaluation artifacts are **not committed** (aside from smoke tests). Real results live in GCP.

## TL;DR

- **GCP project:** `msds-603-victors-demons`
- **Region:** `us-central1`
- **GCS bucket:** `gs://msds603-mlflow-artifacts/models/distilbert-priority/runs/`
- **Vertex AI Model Registry:** model `distilbert-priority` (id `174724492281511936`)
- **Vertex AI Experiments:** experiment name `distilbert-priority`

## What gets produced per run

The evaluation step ([training/evaluate.py](training/evaluate.py)) writes five JSON files into the run's artifact directory:

| File | Contents |
| --- | --- |
| `metrics.json` | overall accuracy, macro F1, per-class precision/recall/F1/support |
| `confusion_matrix.json` | 4×4 matrix + label order |
| `misclassified.json` | up to 20 wrong examples (true vs predicted) |
| `train_summary.json` | train_loss, runtime, sample counts |
| `training_args.json` | hyperparameters (lr, batch size, epochs, seed, data_version) |

Plus the model weights under `model/`.

## Where to find them

### 1. Smoke-test runs (local, committed)

Tiny runs (~10 test samples, not meaningful) live in [training/artifacts/](training/artifacts/):

- [training/artifacts/smoke-20260413-185041/](training/artifacts/smoke-20260413-185041/)
- [training/artifacts/smoke-20260418-232844/](training/artifacts/smoke-20260418-232844/)

### 2. Cloud runs (GCS — this is the real evaluation data)

```bash
gsutil ls gs://msds603-mlflow-artifacts/models/distilbert-priority/runs/
# run-20260419-124917/
# run-20260419-133519/
# run-20260419-140149/

# Overall + per-class metrics
gsutil cat gs://msds603-mlflow-artifacts/models/distilbert-priority/runs/<RUN_ID>/metrics.json

# Confusion matrix
gsutil cat gs://msds603-mlflow-artifacts/models/distilbert-priority/runs/<RUN_ID>/confusion_matrix.json

# Example errors
gsutil cat gs://msds603-mlflow-artifacts/models/distilbert-priority/runs/<RUN_ID>/misclassified.json
```

### 3. Vertex AI Model Registry (headline numbers as labels)

Each registered version carries `accuracy` and `macro-f1` as labels (dots replaced with dashes — Vertex label rules) plus a human-readable `description`:

```bash
gcloud ai models list --region=us-central1 --project=msds-603-victors-demons

gcloud ai models list-version 174724492281511936 \
  --region=us-central1 --project=msds-603-victors-demons --format=json
```

Fields to look at per version: `description`, `labels.accuracy`, `labels.macro-f1`, `labels.run-id`, `artifactUri` (points to the GCS run dir above).

### 4. Vertex AI Experiments (per-run params + metrics)

Logged from [training/vertex_logging.py](training/vertex_logging.py), invoked in [training/train.py](training/train.py) when run with `--cloud`. Browse in the GCP console:

```
https://console.cloud.google.com/vertex-ai/experiments?project=msds-603-victors-demons
```

Metrics logged: `accuracy`, `macro_f1`, `precision_{label}`, `recall_{label}`, `f1_{label}`.
Params logged: `learning_rate`, `batch_size`, `epochs`, `seed`, `data_version`, `run_id`.
