"""Environment-driven configuration for the backend API.

All runtime knobs are read from environment variables so that the same
container image can be pointed at different GCP projects / Cloud SQL
instances / model endpoints across local-dev and Cloud Run deployments.

Constants here are intentionally simple Python strings (no pydantic
BaseSettings) to keep the import graph cheap — this module must remain
free of SDK imports so unit tests can import the app without pulling in
google-auth.
"""
from __future__ import annotations

import os

# --- Model endpoint ---------------------------------------------------------
# Full https URL of the IAM-restricted Cloud Run service that serves the
# DistilBERT priority classifier. Populated by the deploy command via
# `gcloud run services describe distilbert-priority-online` so the backend
# doesn't need to discover it at startup.
MODEL_ENDPOINT_URL_ENV: str = "MODEL_ENDPOINT_URL"

# --- Cloud SQL --------------------------------------------------------------
# Instance connection name in the form "project:region:instance". Consumed
# by google.cloud.sql.connector.Connector.connect().
DB_INSTANCE_ENV: str = "DB_INSTANCE"
DB_NAME_ENV: str = "DB_NAME"
DB_USER_ENV: str = "DB_USER"

# Name (not value) of the Secret Manager secret that holds the app_user
# password. Value is fetched at startup via google-cloud-secret-manager.
DB_PASSWORD_SECRET_ENV: str = "DB_PASSWORD_SECRET"

# --- GCP --------------------------------------------------------------------
# Project id used when looking up Secret Manager secrets.
GCP_PROJECT_ENV: str = "GCP_PROJECT"

# --- Validation -------------------------------------------------------------
TICKET_TEXT_MAX_CHARS: int = 10_000
INPUT_PREVIEW_MAX_CHARS: int = 100

# Canonical priority ordering, most urgent first. Used by GET /tickets to
# sort results. Anything not in this list (e.g. "unknown") is pushed to
# the end of the list.
PRIORITY_ORDER: tuple[str, ...] = ("urgent", "high", "medium", "low")

VALID_VERDICTS: frozenset[str] = frozenset({"thumbs_up", "thumbs_down"})


def require_env(name: str) -> str:
    """Fetch an env var or raise a clear error if unset.

    Used by startup code paths that genuinely cannot function without the
    variable (e.g. MODEL_ENDPOINT_URL). Tests that stub out dependencies
    never call this.
    """
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable {name!r} is not set")
    return value
