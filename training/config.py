"""Centralized hyperparameters and GCP config for DistilBERT priority training."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

# --- Label schema -----------------------------------------------------------
# Ordinal order: low < medium < high < urgent.
LABELS: List[str] = ["low", "medium", "high", "urgent"]
LABEL2ID = {label: i for i, label in enumerate(LABELS)}
ID2LABEL = {i: label for i, label in enumerate(LABELS)}
NUM_LABELS = len(LABELS)

# --- Paths ------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_DATA_PATH = REPO_ROOT / "synthetic_data" / "data" / "tickets.csv"
ARTIFACTS_DIR = REPO_ROOT / "training" / "artifacts"

# --- GCP config (used in Phase 2/3) ----------------------------------------
GCP_PROJECT = "msds-603-victors-demons"
GCP_REGION = "us-central1"
GCS_BUCKET = "msds603-mlflow-artifacts"
DATA_VERSION_DEFAULT = "v1"


def gcs_data_uri(version: str = DATA_VERSION_DEFAULT) -> str:
    return f"gs://{GCS_BUCKET}/data/tickets/{version}/tickets.csv"


def gcs_model_uri(run_id: str) -> str:
    return f"gs://{GCS_BUCKET}/models/distilbert-priority/runs/{run_id}/"


# --- Training config -------------------------------------------------------
@dataclass
class TrainConfig:
    # Model
    base_model: str = "distilbert-base-uncased"
    num_labels: int = NUM_LABELS
    max_length: int = 256

    # Optimization
    learning_rate: float = 2e-5
    batch_size: int = 16
    epochs: int = 3
    warmup_ratio: float = 0.1
    weight_decay: float = 0.01

    # Data split (stratified)
    train_size: float = 0.8
    val_size: float = 0.1
    test_size: float = 0.1

    # Reproducibility
    seed: int = 42

    # Columns
    text_column: str = "ticket_text"
    label_column: str = "priority"

    # Label schema (also exposed here for convenience)
    labels: List[str] = field(default_factory=lambda: list(LABELS))


@dataclass
class SmokeTestConfig(TrainConfig):
    """Override defaults for fast local smoke-testing on CPU."""
    max_rows: int = 100
    epochs: int = 1
    batch_size: int = 4
