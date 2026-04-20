"""Tests for training.evaluate."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from training.config import LABELS
from training.evaluate import (
    compute_confusion_matrix,
    compute_metrics,
    sample_misclassifications,
    save_eval_artifacts,
)


def test_compute_metrics_perfect_predictions():
    # LABELS = ['low','medium','high','urgent'] → ids 0..3
    y_true = [0, 1, 2, 3, 0, 1, 2, 3]
    y_pred = [0, 1, 2, 3, 0, 1, 2, 3]
    m = compute_metrics(y_true, y_pred)
    assert m["accuracy"] == pytest.approx(1.0)
    assert m["macro_f1"] == pytest.approx(1.0)
    for label in LABELS:
        assert m["per_class"][label]["precision"] == pytest.approx(1.0)
        assert m["per_class"][label]["recall"] == pytest.approx(1.0)
        assert m["per_class"][label]["f1"] == pytest.approx(1.0)
        assert m["per_class"][label]["support"] == 2


def test_compute_metrics_known_hand_computed():
    # 4 samples, one per class. Predict last one wrong (urgent -> high).
    y_true = [0, 1, 2, 3]
    y_pred = [0, 1, 2, 2]
    m = compute_metrics(y_true, y_pred)

    # Accuracy: 3/4 = 0.75
    assert m["accuracy"] == pytest.approx(0.75)

    # urgent (id 3): precision 0/0 -> 0 (zero_division=0), recall 0/1 = 0, f1 = 0
    assert m["per_class"]["urgent"]["precision"] == pytest.approx(0.0)
    assert m["per_class"]["urgent"]["recall"] == pytest.approx(0.0)
    assert m["per_class"]["urgent"]["f1"] == pytest.approx(0.0)

    # high (id 2): predicted twice, correct once -> precision 1/2 = 0.5, recall 1/1 = 1.0
    # f1 = 2*0.5*1.0/(0.5+1.0) = 2/3
    assert m["per_class"]["high"]["precision"] == pytest.approx(0.5)
    assert m["per_class"]["high"]["recall"] == pytest.approx(1.0)
    assert m["per_class"]["high"]["f1"] == pytest.approx(2 / 3)

    # low and medium are perfect.
    assert m["per_class"]["low"]["f1"] == pytest.approx(1.0)
    assert m["per_class"]["medium"]["f1"] == pytest.approx(1.0)

    # Macro F1 = mean of the four per-class F1s.
    expected_macro = np.mean([1.0, 1.0, 2 / 3, 0.0])
    assert m["macro_f1"] == pytest.approx(expected_macro)


def test_confusion_matrix_shape_and_counts():
    y_true = [0, 1, 2, 3, 0]
    y_pred = [0, 1, 2, 2, 1]
    cm = compute_confusion_matrix(y_true, y_pred)
    assert cm.shape == (len(LABELS), len(LABELS))
    # Row 0 (low): one correct (0->0), one wrong (0->1).
    assert cm[0, 0] == 1
    assert cm[0, 1] == 1
    # Row 3 (urgent): one wrong (3->2).
    assert cm[3, 2] == 1


def test_sample_misclassifications_basic():
    texts = ["a", "b", "c", "d"]
    y_true = [0, 1, 2, 3]
    y_pred = [0, 2, 2, 0]  # wrong at indices 1 and 3
    samples = sample_misclassifications(texts, y_true, y_pred, n=10)
    assert len(samples) == 2
    indices = {s["index"] for s in samples}
    assert indices == {1, 3}
    for s in samples:
        assert s["true_label"] in LABELS
        assert s["pred_label"] in LABELS


def test_sample_misclassifications_none_when_perfect():
    samples = sample_misclassifications(["a", "b"], [0, 1], [0, 1], n=5)
    assert samples == []


def test_save_eval_artifacts_writes_files(tmp_path: Path):
    metrics = {"accuracy": 0.9, "macro_f1": 0.88, "per_class": {}}
    cm = np.zeros((4, 4), dtype=int)
    cm[0, 0] = 3
    misclassified = [{"index": 1, "text": "x", "true_label": "low", "pred_label": "high"}]

    save_eval_artifacts(tmp_path, metrics, cm, misclassified)

    assert (tmp_path / "metrics.json").exists()
    assert (tmp_path / "confusion_matrix.json").exists()
    assert (tmp_path / "misclassified.json").exists()

    cm_payload = json.loads((tmp_path / "confusion_matrix.json").read_text())
    assert cm_payload["labels"] == LABELS
    assert cm_payload["matrix"][0][0] == 3
