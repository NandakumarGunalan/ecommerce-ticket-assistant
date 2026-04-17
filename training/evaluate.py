"""Metrics, confusion matrix, and misclassification sampling."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)

from training.config import ID2LABEL, LABELS


def compute_metrics(y_true: Sequence[int], y_pred: Sequence[int]) -> Dict:
    """Compute accuracy, macro F1, and per-class precision/recall/F1."""
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)

    accuracy = float(accuracy_score(y_true, y_pred))
    macro_f1 = float(f1_score(y_true, y_pred, average="macro", zero_division=0))

    label_ids = list(range(len(LABELS)))
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=label_ids, zero_division=0
    )
    per_class = {
        LABELS[i]: {
            "precision": float(precision[i]),
            "recall": float(recall[i]),
            "f1": float(f1[i]),
            "support": int(support[i]),
        }
        for i in label_ids
    }
    return {
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "per_class": per_class,
    }


def compute_confusion_matrix(
    y_true: Sequence[int], y_pred: Sequence[int]
) -> np.ndarray:
    """Return a (num_labels, num_labels) confusion matrix (rows = true)."""
    label_ids = list(range(len(LABELS)))
    return confusion_matrix(y_true, y_pred, labels=label_ids)


def sample_misclassifications(
    texts: Sequence[str],
    y_true: Sequence[int],
    y_pred: Sequence[int],
    n: int = 20,
    seed: int = 42,
) -> List[Dict]:
    """Return up to `n` misclassified examples for error analysis."""
    rng = np.random.default_rng(seed)
    texts = list(texts)
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)

    wrong_idx = np.where(y_true != y_pred)[0]
    if len(wrong_idx) == 0:
        return []
    if len(wrong_idx) > n:
        wrong_idx = rng.choice(wrong_idx, size=n, replace=False)

    samples = []
    for i in wrong_idx:
        samples.append(
            {
                "index": int(i),
                "text": texts[int(i)],
                "true_label": ID2LABEL[int(y_true[i])],
                "pred_label": ID2LABEL[int(y_pred[i])],
            }
        )
    return samples


def hf_compute_metrics(eval_pred) -> Dict[str, float]:
    """HuggingFace `Trainer`-compatible compute_metrics callback.

    Returns only scalar metrics (accuracy, macro_f1, and per-class f1s).
    """
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=-1)
    out = compute_metrics(labels, preds)
    flat = {
        "accuracy": out["accuracy"],
        "macro_f1": out["macro_f1"],
    }
    for cls, d in out["per_class"].items():
        flat[f"f1_{cls}"] = d["f1"]
    return flat


def save_eval_artifacts(
    out_dir: Path,
    metrics: Dict,
    cm: np.ndarray,
    misclassified: List[Dict],
    test_predictions: Optional[Dict] = None,
) -> None:
    """Dump metrics.json, confusion_matrix.json, misclassified.json."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    cm_payload = {
        "labels": LABELS,
        "matrix": cm.tolist(),
    }
    with open(out_dir / "confusion_matrix.json", "w") as f:
        json.dump(cm_payload, f, indent=2)

    with open(out_dir / "misclassified.json", "w") as f:
        json.dump(misclassified, f, indent=2)

    if test_predictions is not None:
        with open(out_dir / "test_predictions.json", "w") as f:
            json.dump(test_predictions, f, indent=2)
