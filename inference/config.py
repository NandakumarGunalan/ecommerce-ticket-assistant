"""Centralized inference-side constants for the DistilBERT priority serving container.

Import-safe: no google-cloud-* SDK imports here. Anything heavier (Vertex Registry
clients, Cloud Logging clients, DB clients) lives in the modules that need it.
"""
from __future__ import annotations

from typing import Dict, List

# --- GCP config -------------------------------------------------------------
GCP_PROJECT: str = "msds-603-victors-demons"
GCP_REGION: str = "us-central1"
# Historical name (predates the MLflow→Vertex migration); reused for model artifacts.
GCS_BUCKET: str = "msds603-mlflow-artifacts"

# --- Model Registry --------------------------------------------------------
# Must match the display_name used by training/registry.py when registering.
MODEL_DISPLAY_NAME: str = "distilbert-priority"

# Env var name that, when set, pins the loader to a specific registered version
# (e.g. MODEL_VERSION=2). When unset, the loader resolves to the current default.
MODEL_VERSION_ENV: str = "MODEL_VERSION"

# --- Label schema ----------------------------------------------------------
# Ordinal order: low < medium < high < urgent. Must stay in lockstep with
# training/config.py so that argmax indices map to the same label strings.
LABELS: List[str] = ["low", "medium", "high", "urgent"]
LABEL2ID: Dict[str, int] = {label: i for i, label in enumerate(LABELS)}
ID2LABEL: Dict[int, str] = {i: label for i, label in enumerate(LABELS)}

# --- Tokenization ---------------------------------------------------------
# Must match training's max_length so inference-time truncation behaves the same
# way the model was trained under.
MAX_LENGTH: int = 256

# --- Cloud SQL connection --------------------------------------------------
# Resolved from env at runtime (not baked into the image) so the same container
# can point at different instances / databases across environments. The IAM
# user is typically the inference service account email with the trailing
# "@<project>.iam.gserviceaccount.com" suffix stripped, per the Cloud SQL IAM
# auth convention.
CLOUD_SQL_CONNECTION_NAME_ENV: str = "CLOUD_SQL_CONNECTION_NAME"  # project:region:instance
CLOUD_SQL_DB_NAME_ENV: str = "CLOUD_SQL_DB_NAME"
CLOUD_SQL_DB_USER_ENV: str = "CLOUD_SQL_DB_USER"
