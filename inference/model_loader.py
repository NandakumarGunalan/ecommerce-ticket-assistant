"""Resolve + load the DistilBERT priority model from Vertex AI Model Registry.

Resolution order:
  1. If the ``MODEL_VERSION`` env var (name configured in ``inference.config``)
     is set, load that specific Vertex Model version.
  2. Else, list models with ``display_name == MODEL_DISPLAY_NAME`` ordered by
     ``create_time desc`` and pick the most recent one flagged as default.

The loaded model/tokenizer, the resolved Vertex Registry version id, and the
training ``run_id`` (parsed from ``artifact_uri``) are cached as a module-level
singleton so subsequent ``load_model()`` calls return immediately.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from google.cloud import aiplatform, storage
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from inference.config import (
    GCP_PROJECT,
    GCP_REGION,
    MODEL_DISPLAY_NAME,
    MODEL_VERSION_ENV,
)
from inference.logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class LoadedModel:
    """Bundle returned by :func:`load_model`."""

    model: "AutoModelForSequenceClassification"
    tokenizer: "AutoTokenizer"
    model_version: str
    model_run_id: str


# --- Module-level singleton cache ------------------------------------------
_loaded: Optional[LoadedModel] = None
_lock = threading.Lock()


def _reset_cache_for_testing() -> None:
    """Reset the module-level cache. For tests only."""
    global _loaded
    with _lock:
        _loaded = None


def _parse_run_id_from_artifact_uri(artifact_uri: str) -> str:
    """Extract ``run_id`` from a URI shaped like
    ``gs://bucket/.../runs/<run_id>/model/`` (or ``/model`` without trailing
    slash).

    The run id is the directory component immediately before ``model``.
    """
    # Normalize: strip trailing slash, then split into parts.
    stripped = artifact_uri.rstrip("/")
    parts = stripped.split("/")
    if not parts:
        raise ValueError("Cannot parse run_id from empty artifact_uri")
    if parts[-1] == "model" and len(parts) >= 2:
        return parts[-2]
    # Fallback: if the URI does not end with /model, just take the last
    # component as the run id.
    return parts[-1]


def _download_gcs_dir(gs_uri: str, local_dir: str) -> None:
    """Download every blob under ``gs_uri`` (a ``gs://bucket/prefix/``) into
    ``local_dir``, preserving relative structure (flattened to the prefix).
    """
    parsed = urlparse(gs_uri)
    if parsed.scheme != "gs":
        raise ValueError(f"Expected gs:// URI, got: {gs_uri}")
    bucket_name = parsed.netloc
    prefix = parsed.path.lstrip("/")
    if not prefix.endswith("/"):
        prefix = prefix + "/"

    client = storage.Client(project=GCP_PROJECT)
    bucket = client.bucket(bucket_name)
    blobs = list(client.list_blobs(bucket_or_name=bucket, prefix=prefix))
    if not blobs:
        raise RuntimeError(
            f"No blobs found under {gs_uri} — cannot load model artifacts"
        )

    for blob in blobs:
        rel = blob.name[len(prefix):]
        if not rel or rel.endswith("/"):
            # Skip the "directory" marker blob itself.
            continue
        dest_path = os.path.join(local_dir, rel)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        blob.download_to_filename(dest_path)


def _resolve_model_version() -> aiplatform.Model:
    """Resolve which Vertex Registry model version to load.

    - If ``MODEL_VERSION_ENV`` is set in the environment, load that specific
      version by passing ``"<display_name>@<version>"`` as ``model_name``.
    - Else, pick the default version among models with the matching display
      name, most recently created first.
    """
    aiplatform.init(project=GCP_PROJECT, location=GCP_REGION)

    pinned = os.getenv(MODEL_VERSION_ENV)
    if pinned:
        resource_name = f"{MODEL_DISPLAY_NAME}@{pinned}"
        logger.info(
            "Resolving pinned model version",
            extra={"model_display_name": MODEL_DISPLAY_NAME, "version": pinned},
        )
        return aiplatform.Model(model_name=resource_name)

    logger.info("Resolving model via Vertex Registry (default version)...")
    candidates = aiplatform.Model.list(
        filter=f'display_name="{MODEL_DISPLAY_NAME}"',
        order_by="create_time desc",
    )
    if not candidates:
        raise RuntimeError(
            f"No models registered with display_name={MODEL_DISPLAY_NAME}"
        )

    for candidate in candidates:
        # The SDK exposes the default-version flag under a couple of names
        # depending on release; we check the most common ones plus alias.
        is_default = (
            getattr(candidate, "is_default_version", False)
            or "default" in (getattr(candidate, "version_aliases", None) or [])
        )
        if is_default:
            return candidate

    # Fall back to the most recently created one if nothing is flagged default.
    logger.warning(
        "No model flagged as default version; using most recent by create_time"
    )
    return candidates[0]


def load_model() -> LoadedModel:
    """Resolve the model from Vertex Registry, download artifacts from GCS,
    load the HuggingFace model + tokenizer, and cache the result.

    Subsequent calls return the cached :class:`LoadedModel` without re-running
    any of the above work.
    """
    global _loaded
    if _loaded is not None:
        return _loaded

    with _lock:
        if _loaded is not None:
            return _loaded

        start = time.time()

        vertex_model = _resolve_model_version()
        version_id = str(getattr(vertex_model, "version_id", "") or "")
        artifact_uri = getattr(vertex_model, "uri", None) or getattr(
            vertex_model, "artifact_uri", None
        )
        if not artifact_uri:
            raise RuntimeError(
                "Resolved Vertex model has no artifact_uri; cannot download "
                "artifacts"
            )
        aliases = getattr(vertex_model, "version_aliases", None) or []

        logger.info(
            "Using model version",
            extra={
                "model_version": version_id,
                "version_aliases": list(aliases),
                "artifact_uri": artifact_uri,
            },
        )

        model_run_id = _parse_run_id_from_artifact_uri(artifact_uri)

        # Use a persistent temp dir so the loaded HF objects keep working even
        # after this function returns. Cloud Run containers are ephemeral, so
        # we don't need to clean up ourselves.
        local_dir = tempfile.mkdtemp(prefix="distilbert-priority-")
        logger.info(
            "Downloading artifacts",
            extra={"artifact_uri": artifact_uri, "local_dir": local_dir},
        )
        _download_gcs_dir(artifact_uri, local_dir)

        logger.info("Loading HuggingFace model + tokenizer from local dir")
        model = AutoModelForSequenceClassification.from_pretrained(local_dir)
        tokenizer = AutoTokenizer.from_pretrained(local_dir)
        model.eval()

        elapsed = time.time() - start
        logger.info(
            "Model loaded",
            extra={
                "elapsed_sec": round(elapsed, 2),
                "model_version": version_id,
                "model_run_id": model_run_id,
            },
        )

        _loaded = LoadedModel(
            model=model,
            tokenizer=tokenizer,
            model_version=version_id,
            model_run_id=model_run_id,
        )
        return _loaded
