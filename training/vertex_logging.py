"""Vertex AI Experiments logging — thin wrapper so train.py stays clean."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Dict

from google.cloud import aiplatform

from training.config import TrainConfig

EXPERIMENT_NAME = "distilbert-priority"


def init_experiment(project: str, region: str, run_id: str) -> None:
    """Initialize Vertex AI and start an Experiment run."""
    aiplatform.init(project=project, location=region, experiment=EXPERIMENT_NAME)
    aiplatform.start_run(run_id)


def log_params(config: TrainConfig, extra: Dict[str, str] | None = None) -> None:
    params = {k: v for k, v in asdict(config).items() if not isinstance(v, (list, dict))}
    params = {k: (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in params.items()}
    if extra:
        params.update({k: str(v) for k, v in extra.items()})
    aiplatform.log_params(params)


def log_metrics(metrics: Dict) -> None:
    flat: Dict[str, float] = {
        "accuracy": float(metrics["accuracy"]),
        "macro_f1": float(metrics["macro_f1"]),
    }
    for cls, d in metrics["per_class"].items():
        flat[f"precision_{cls}"] = float(d["precision"])
        flat[f"recall_{cls}"] = float(d["recall"])
        flat[f"f1_{cls}"] = float(d["f1"])
    aiplatform.log_metrics(flat)


def log_artifacts_dir(local_dir: Path) -> None:
    """Vertex Experiments doesn't natively upload dirs; we rely on GCS for that.
    This function is a placeholder so callers have one API surface — the actual
    artifact upload happens via gcs_io.upload_directory."""
    return None


def end_run() -> None:
    aiplatform.end_run()
