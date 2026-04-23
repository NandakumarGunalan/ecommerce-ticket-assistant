"""Core prediction logic for the DistilBERT priority classifier.

This module is the shared inference core used by both the batch CLI
(`inference.batch_predict`) and the future online FastAPI endpoint
(`inference.main`). It is deliberately transport-agnostic: it takes raw
ticket texts in, returns :class:`~inference.schemas.Prediction` objects out,
and does not know anything about HTTP, DBs, or files.

See ``inference/PLAN.md`` section "Prediction Request/Response Shape" for the
return contract. Per-prediction structured logging lives in
``inference/batch_predict.py`` (future wave), not here.
"""
from __future__ import annotations

from typing import List, Optional

import torch

from inference.config import ID2LABEL, LABELS, MAX_LENGTH
from inference.logging_utils import get_logger
from inference.model_loader import load_model
from inference.schemas import Prediction

logger = get_logger(__name__)


def _iter_batches(n: int, batch_size: int):
    """Yield ``(start, end)`` index pairs covering ``range(n)`` in
    ``batch_size`` chunks.
    """
    if batch_size <= 0:
        raise ValueError(f"batch_size must be positive, got {batch_size}")
    for start in range(0, n, batch_size):
        yield start, min(start + batch_size, n)


def predict(
    texts: List[str],
    ids: Optional[List[str]] = None,
    *,
    batch_size: int = 32,
) -> List[Prediction]:
    """Run inference on a list of raw ticket texts.

    Parameters
    ----------
    texts:
        The raw ticket texts to score, in the order results should be returned.
    ids:
        Optional parallel list of ticket ids. If provided, length must match
        ``texts`` exactly; each returned ``Prediction.id`` is populated in the
        same order. If ``None``, all returned ``Prediction.id`` values are
        ``None`` (the online single-ticket path).
    batch_size:
        Size of each forward pass. Safe for arbitrarily large ``texts``.

    Returns
    -------
    list[Prediction]
        One :class:`Prediction` per input text, in the same order as ``texts``.
        ``all_scores`` is a dict mapping every label in
        :data:`inference.config.LABELS` to its softmax probability (values sum
        to ~1); ``confidence`` is the max of those; ``predicted_priority`` is
        the argmax label.

    Notes
    -----
    - Empty ``texts`` returns ``[]`` without loading the model.
    - Texts longer than :data:`inference.config.MAX_LENGTH` tokens are
      truncated by the tokenizer. Empty strings are scored normally.
    - The model is set to ``.eval()`` mode and the forward pass runs under
      :func:`torch.inference_mode`.
    """
    if ids is not None and len(ids) != len(texts):
        raise ValueError(
            f"ids length ({len(ids)}) does not match texts length ({len(texts)})"
        )

    if not texts:
        return []

    loaded = load_model()
    model = loaded.model
    tokenizer = loaded.tokenizer
    model.eval()

    n = len(texts)
    # Precompute number of batches for the log line below.
    batches = (n + batch_size - 1) // batch_size
    logger.info(
        f"predict: n={n} batches={batches} model_version={loaded.model_version}"
    )

    results: List[Prediction] = []
    with torch.inference_mode():
        for start, end in _iter_batches(n, batch_size):
            batch_texts = texts[start:end]
            encoded = tokenizer(
                batch_texts,
                truncation=True,
                max_length=MAX_LENGTH,
                padding="longest",
                return_tensors="pt",
            )
            outputs = model(**encoded)
            logits = outputs.logits
            probs = torch.softmax(logits, dim=-1)
            # argmax per row
            argmax_ids = torch.argmax(probs, dim=-1).tolist()
            probs_list = probs.tolist()

            for i, row_probs in enumerate(probs_list):
                all_scores = {label: float(row_probs[idx]) for idx, label in enumerate(LABELS)}
                pred_id = argmax_ids[i]
                pred_label = ID2LABEL[pred_id]
                results.append(
                    Prediction(
                        id=(ids[start + i] if ids is not None else None),
                        predicted_priority=pred_label,
                        confidence=float(row_probs[pred_id]),
                        all_scores=all_scores,
                    )
                )

    return results
