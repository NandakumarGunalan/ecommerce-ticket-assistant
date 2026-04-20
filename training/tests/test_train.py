"""Smoke test for the end-to-end training loop on CPU."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from training.config import SmokeTestConfig
from training.train import train


def _make_fixture_csv(tmp_path: Path, rows_per_class: int = 5) -> Path:
    rows = []
    for label in ["low", "medium", "high", "urgent"]:
        for i in range(rows_per_class):
            rows.append(
                {
                    "ticket_id": f"{label}-{i}",
                    "ticket_text": (
                        f"Ticket {i} concerning a {label} priority issue. "
                        f"Please address this as soon as possible."
                    ),
                    "priority": label,
                }
            )
    df = pd.DataFrame(rows)
    path = tmp_path / "fixture_tickets.csv"
    df.to_csv(path, index=False)
    return path


@pytest.mark.slow
def test_smoke_train_end_to_end(tmp_path: Path):
    """Tiny 20-row fixture, 1 epoch, bs=4, short max_length. CPU-only."""
    csv_path = _make_fixture_csv(tmp_path, rows_per_class=10)
    out_dir = tmp_path / "artifacts" / "smoke"

    # Override: tiny max_length to keep this under a minute on CPU.
    cfg = SmokeTestConfig(max_length=32)

    result = train(
        data_path=str(csv_path),
        config=cfg,
        output_dir=out_dir,
        max_rows=None,  # fixture is already tiny
    )

    # --- Metrics dict has expected keys ---
    metrics = result["metrics"]
    assert "accuracy" in metrics
    assert "macro_f1" in metrics
    assert "per_class" in metrics
    assert 0.0 <= metrics["accuracy"] <= 1.0
    assert 0.0 <= metrics["macro_f1"] <= 1.0

    # --- Artifacts saved ---
    assert (out_dir / "metrics.json").exists()
    assert (out_dir / "confusion_matrix.json").exists()
    assert (out_dir / "misclassified.json").exists()
    assert (out_dir / "training_args.json").exists()
    assert (out_dir / "train_summary.json").exists()

    # --- Model saved ---
    model_dir = out_dir / "model"
    assert model_dir.exists()
    # HF saves either pytorch_model.bin or model.safetensors.
    saved_weights = list(model_dir.glob("*.bin")) + list(model_dir.glob("*.safetensors"))
    assert saved_weights, f"No model weights found in {model_dir}"
    assert (model_dir / "config.json").exists()
    # Tokenizer artifacts.
    assert (model_dir / "tokenizer_config.json").exists()

    # --- metrics.json round-trip ---
    saved_metrics = json.loads((out_dir / "metrics.json").read_text())
    assert saved_metrics["accuracy"] == metrics["accuracy"]
