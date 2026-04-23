"""Unit tests for :mod:`inference.predictor`.

All tests mock ``inference.model_loader.load_model`` so they do not touch GCP,
Vertex AI, or the network. The mocked model returns a real
:class:`torch.Tensor` for ``.logits`` so the softmax/argmax path exercises the
actual tensor math.
"""
from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import torch

from inference import predictor as predictor_mod
from inference.config import LABELS
from inference.schemas import Prediction


def _make_fake_loaded_model(logits_tensor: torch.Tensor) -> SimpleNamespace:
    """Build a fake ``LoadedModel``-shaped object.

    The tokenizer is a MagicMock whose ``__call__`` returns a dict-like
    object (actually a real dict — ``model(**encoded)`` will happily unpack
    it). The model is a MagicMock whose ``__call__`` returns an object with a
    ``.logits`` attribute equal to ``logits_tensor``. ``eval()`` is a no-op.
    """
    tokenizer = MagicMock(name="tokenizer")
    # tokenizer(texts, ...) → dict (kwargs-unpackable into model(**...))
    tokenizer.return_value = {"input_ids": torch.zeros((1, 1), dtype=torch.long)}

    model = MagicMock(name="model")
    model.return_value = SimpleNamespace(logits=logits_tensor)
    model.eval = MagicMock(return_value=None)

    return SimpleNamespace(
        model=model,
        tokenizer=tokenizer,
        model_version="test-version",
        model_run_id="run-test",
    )


@pytest.fixture(autouse=True)
def _reset_loader_cache():
    """Ensure the module-level cache in ``inference.model_loader`` is clean
    between tests so each test's patched ``load_model`` is actually seen.
    """
    from inference import model_loader

    model_loader._reset_cache_for_testing()
    yield
    model_loader._reset_cache_for_testing()


@pytest.fixture
def patch_load_model():
    """Factory fixture that patches ``inference.predictor.load_model``.

    The predictor module imports ``load_model`` directly, so we patch the
    reference on the predictor module (not on ``inference.model_loader``).
    """
    patches = []

    def _apply(logits_per_call):
        """``logits_per_call`` may be a single tensor (returned every call)
        or a list of tensors (one per forward pass / batch).
        """
        loaded = _make_fake_loaded_model(
            logits_per_call if isinstance(logits_per_call, torch.Tensor) else logits_per_call[0]
        )
        if isinstance(logits_per_call, list):
            # Return a new SimpleNamespace(.logits=t) for each call.
            loaded.model.side_effect = [SimpleNamespace(logits=t) for t in logits_per_call]
            loaded.model.return_value = None

        p = patch.object(predictor_mod, "load_model", return_value=loaded)
        p.start()
        patches.append(p)
        return loaded

    yield _apply

    for p in patches:
        p.stop()


# 1. Shape
def test_predict_single_text_returns_one_prediction(patch_load_model):
    logits = torch.tensor([[0.1, 0.2, 0.3, 0.4]])
    patch_load_model(logits)

    results = predictor_mod.predict(["hello"])

    assert isinstance(results, list)
    assert len(results) == 1
    assert isinstance(results[0], Prediction)
    assert set(results[0].all_scores.keys()) == set(LABELS)
    total = sum(results[0].all_scores.values())
    assert math.isclose(total, 1.0, abs_tol=1e-5)


# 2. Argmax correctness
def test_predict_argmax_is_high(patch_load_model):
    # class 2 = "high" is the max
    logits = torch.tensor([[0.0, 0.0, 5.0, 0.0]])
    patch_load_model(logits)

    results = predictor_mod.predict(["anything"])
    pred = results[0]

    expected = torch.softmax(logits, dim=-1)[0].tolist()
    assert pred.predicted_priority == "high"
    assert math.isclose(pred.confidence, max(expected), abs_tol=1e-6)
    assert math.isclose(pred.confidence, pred.all_scores["high"], abs_tol=1e-6)


# 3. Label order
def test_all_scores_keys_match_label_order(patch_load_model):
    logits = torch.tensor([[0.1, 0.2, 0.3, 0.4]])
    patch_load_model(logits)

    results = predictor_mod.predict(["x"])
    keys = list(results[0].all_scores.keys())
    assert keys == LABELS


# 4. IDs pass-through
def test_ids_pass_through(patch_load_model):
    logits = torch.tensor(
        [
            [0.0, 0.0, 0.0, 1.0],  # urgent
            [1.0, 0.0, 0.0, 0.0],  # low
        ]
    )
    patch_load_model(logits)

    results = predictor_mod.predict(["a", "b"], ids=["T-1", "T-2"])

    assert [r.id for r in results] == ["T-1", "T-2"]


# 5. IDs length mismatch
def test_ids_length_mismatch_raises(patch_load_model):
    # load_model must not be called if we fail validation, but the patch is
    # harmless either way.
    with pytest.raises(ValueError, match="length"):
        predictor_mod.predict(["a", "b"], ids=["only-one"])


# 6. Empty input
def test_empty_input_returns_empty_list_and_does_not_load_model():
    with patch.object(predictor_mod, "load_model") as mock_load:
        results = predictor_mod.predict([])
        assert results == []
        mock_load.assert_not_called()


# 7. Batching
def test_batching_calls_model_once_per_batch(patch_load_model):
    # 100 inputs, batch_size=32 → ceil(100/32) = 4 batches of sizes 32,32,32,4.
    batch_logits = [
        torch.zeros((32, 4)),
        torch.zeros((32, 4)),
        torch.zeros((32, 4)),
        torch.zeros((4, 4)),
    ]
    loaded = patch_load_model(batch_logits)

    results = predictor_mod.predict(["t"] * 100, batch_size=32)

    assert len(results) == 100
    assert loaded.model.call_count == 4


# 8. Empty string
def test_empty_string_input_does_not_crash(patch_load_model):
    logits = torch.tensor([[0.25, 0.25, 0.25, 0.25]])
    patch_load_model(logits)

    results = predictor_mod.predict([""])

    assert len(results) == 1
    assert isinstance(results[0], Prediction)
    assert set(results[0].all_scores.keys()) == set(LABELS)
