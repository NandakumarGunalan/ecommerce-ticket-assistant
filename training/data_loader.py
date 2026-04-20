"""Load tickets.csv, stratify-split, and tokenize for DistilBERT."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd
from datasets import Dataset
from sklearn.model_selection import train_test_split
from transformers import AutoTokenizer, PreTrainedTokenizerBase

from training.config import LABEL2ID, TrainConfig


@dataclass
class SplitDatasets:
    train: Dataset
    val: Dataset
    test: Dataset
    tokenizer: PreTrainedTokenizerBase


def load_dataframe(
    path: str,
    text_column: str = "ticket_text",
    label_column: str = "priority",
    max_rows: Optional[int] = None,
) -> pd.DataFrame:
    """Load tickets CSV from a local path or GCS URI.

    pandas handles `gs://` via fsspec/gcsfs when available (Phase 2). For
    Phase 1 we load from a local filesystem path.
    """
    df = pd.read_csv(path)
    missing = {text_column, label_column} - set(df.columns)
    if missing:
        raise ValueError(f"CSV at {path} is missing columns: {missing}")

    # Drop rows with null text/label, normalize label casing.
    df = df.dropna(subset=[text_column, label_column]).copy()
    df[label_column] = df[label_column].astype(str).str.strip().str.lower()

    valid_labels = set(LABEL2ID.keys())
    bad = set(df[label_column].unique()) - valid_labels
    if bad:
        raise ValueError(f"Unknown priority labels in data: {bad}")

    if max_rows is not None:
        df = df.head(max_rows).reset_index(drop=True)

    return df


def stratified_split(
    df: pd.DataFrame,
    label_column: str,
    train_size: float,
    val_size: float,
    test_size: float,
    seed: int,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """80/10/10 stratified split on label column (ratios configurable)."""
    total = train_size + val_size + test_size
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"split sizes must sum to 1.0 (got {total})")

    # First: split off test.
    train_val_df, test_df = train_test_split(
        df,
        test_size=test_size,
        random_state=seed,
        stratify=df[label_column],
    )
    # Then: split train_val into train and val.
    relative_val = val_size / (train_size + val_size)
    train_df, val_df = train_test_split(
        train_val_df,
        test_size=relative_val,
        random_state=seed,
        stratify=train_val_df[label_column],
    )
    return (
        train_df.reset_index(drop=True),
        val_df.reset_index(drop=True),
        test_df.reset_index(drop=True),
    )


def _to_hf_dataset(
    df: pd.DataFrame,
    tokenizer: PreTrainedTokenizerBase,
    text_column: str,
    label_column: str,
    max_length: int,
) -> Dataset:
    ds = Dataset.from_pandas(
        pd.DataFrame(
            {
                "text": df[text_column].astype(str).tolist(),
                "labels": df[label_column].map(LABEL2ID).astype(int).tolist(),
            }
        )
    )

    def _tokenize(batch):
        return tokenizer(
            batch["text"],
            truncation=True,
            padding="max_length",
            max_length=max_length,
        )

    ds = ds.map(_tokenize, batched=True)
    columns = ["input_ids", "attention_mask", "labels"]
    ds.set_format(type="torch", columns=columns)
    return ds


def load_and_tokenize(
    path: str,
    config: TrainConfig,
    tokenizer: Optional[PreTrainedTokenizerBase] = None,
    max_rows: Optional[int] = None,
) -> SplitDatasets:
    """End-to-end: load CSV, stratified-split, tokenize, return HF Datasets."""
    df = load_dataframe(
        path,
        text_column=config.text_column,
        label_column=config.label_column,
        max_rows=max_rows,
    )
    train_df, val_df, test_df = stratified_split(
        df,
        label_column=config.label_column,
        train_size=config.train_size,
        val_size=config.val_size,
        test_size=config.test_size,
        seed=config.seed,
    )
    if tokenizer is None:
        tokenizer = AutoTokenizer.from_pretrained(config.base_model)

    train_ds = _to_hf_dataset(
        train_df, tokenizer, config.text_column, config.label_column, config.max_length
    )
    val_ds = _to_hf_dataset(
        val_df, tokenizer, config.text_column, config.label_column, config.max_length
    )
    test_ds = _to_hf_dataset(
        test_df, tokenizer, config.text_column, config.label_column, config.max_length
    )

    return SplitDatasets(train=train_ds, val=val_ds, test=test_ds, tokenizer=tokenizer)
