"""Tests for training.data_loader."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from training.config import LABEL2ID, TrainConfig
from training.data_loader import (
    load_and_tokenize,
    load_dataframe,
    stratified_split,
)


# A small in-memory fixture covering all 4 classes with enough rows per class
# to survive a stratified 80/10/10 split (need >= 2 per class after the first
# split, so we use 5 per class = 20 rows total).
def _make_tiny_csv(tmp_path: Path) -> Path:
    rows = []
    labels = ["low", "medium", "high", "urgent"]
    for label in labels:
        for i in range(12):
            rows.append(
                {
                    "ticket_id": f"{label}-{i}",
                    "ticket_text": f"This is a {label} priority ticket number {i}. Please help.",
                    "priority": label,
                }
            )
    df = pd.DataFrame(rows)
    path = tmp_path / "tiny_tickets.csv"
    df.to_csv(path, index=False)
    return path


def test_load_dataframe_has_expected_columns(tmp_path):
    path = _make_tiny_csv(tmp_path)
    df = load_dataframe(str(path))
    assert "ticket_text" in df.columns
    assert "priority" in df.columns
    # Labels should be lowercased and in the valid set.
    assert set(df["priority"].unique()).issubset(set(LABEL2ID.keys()))


def test_load_dataframe_rejects_bad_labels(tmp_path):
    path = tmp_path / "bad.csv"
    pd.DataFrame(
        {"ticket_text": ["hi"], "priority": ["super-urgent"]}
    ).to_csv(path, index=False)
    with pytest.raises(ValueError, match="Unknown priority"):
        load_dataframe(str(path))


def test_load_dataframe_max_rows(tmp_path):
    path = _make_tiny_csv(tmp_path)
    df = load_dataframe(str(path), max_rows=8)
    assert len(df) == 8


def test_stratified_split_preserves_class_presence(tmp_path):
    path = _make_tiny_csv(tmp_path)
    df = load_dataframe(str(path))
    train_df, val_df, test_df = stratified_split(
        df,
        label_column="priority",
        train_size=0.8,
        val_size=0.1,
        test_size=0.1,
        seed=42,
    )
    # All four classes must appear in train (the largest split).
    assert set(train_df["priority"].unique()) == set(LABEL2ID.keys())
    # Splits must be disjoint and cover everything.
    assert len(train_df) + len(val_df) + len(test_df) == len(df)


def test_stratified_split_rejects_bad_ratios(tmp_path):
    path = _make_tiny_csv(tmp_path)
    df = load_dataframe(str(path))
    with pytest.raises(ValueError, match="must sum to"):
        stratified_split(
            df, label_column="priority",
            train_size=0.5, val_size=0.2, test_size=0.1, seed=42,
        )


def test_load_and_tokenize_shapes(tmp_path):
    path = _make_tiny_csv(tmp_path)
    # Use a tiny max_length to keep tokenization fast.
    cfg = TrainConfig(max_length=32)
    splits = load_and_tokenize(str(path), cfg)
    # Each split should have the tokenized columns.
    for ds in (splits.train, splits.val, splits.test):
        assert "input_ids" in ds.column_names
        assert "attention_mask" in ds.column_names
        assert "labels" in ds.column_names
        # padding='max_length' -> every row has exactly max_length tokens.
        row = ds[0]
        assert len(row["input_ids"]) == cfg.max_length
        assert len(row["attention_mask"]) == cfg.max_length
        # Labels must be ints in [0, num_labels).
        assert 0 <= int(row["labels"]) < cfg.num_labels
