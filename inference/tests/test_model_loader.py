"""Unit tests for inference.model_loader.

All GCP calls (Vertex AI and GCS) are mocked — these tests never hit the
network and must pass with no credentials available.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from inference import model_loader
from inference.model_loader import (
    LoadedModel,
    _parse_run_id_from_artifact_uri,
    load_model,
)


ARTIFACT_URI = (
    "gs://msds603-mlflow-artifacts/models/distilbert-priority/"
    "runs/run-20260419-140149/model/"
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset the module singleton between tests."""
    model_loader._reset_cache_for_testing()
    yield
    model_loader._reset_cache_for_testing()


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("MODEL_VERSION", raising=False)


def _make_fake_vertex_model(
    version_id: str = "2",
    artifact_uri: str = ARTIFACT_URI,
    aliases=("default",),
    is_default: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        version_id=version_id,
        uri=artifact_uri,
        artifact_uri=artifact_uri,
        version_aliases=list(aliases),
        is_default_version=is_default,
        resource_name=f"projects/p/locations/us-central1/models/123@{version_id}",
    )


def _patch_heavy_loaders(version_id="2", artifact_uri=ARTIFACT_URI):
    """Patch the GCS download + HF from_pretrained calls so load_model() only
    exercises the resolution/parsing paths."""
    patches = [
        patch.object(model_loader, "_download_gcs_dir", MagicMock()),
        patch.object(
            model_loader,
            "AutoModelForSequenceClassification",
            MagicMock(
                from_pretrained=MagicMock(return_value=MagicMock(name="hf_model"))
            ),
        ),
        patch.object(
            model_loader,
            "AutoTokenizer",
            MagicMock(
                from_pretrained=MagicMock(return_value=MagicMock(name="hf_tok"))
            ),
        ),
    ]
    return patches


def test_parse_run_id_from_artifact_uri_trailing_slash():
    run_id = _parse_run_id_from_artifact_uri(
        "gs://bucket/models/distilbert-priority/runs/run-20260419-140149/model/"
    )
    assert run_id == "run-20260419-140149"


def test_parse_run_id_from_artifact_uri_no_trailing_slash():
    run_id = _parse_run_id_from_artifact_uri(
        "gs://bucket/models/distilbert-priority/runs/run-20260419-140149/model"
    )
    assert run_id == "run-20260419-140149"


def test_load_model_uses_env_override(monkeypatch):
    monkeypatch.setenv("MODEL_VERSION", "5")

    fake_model = _make_fake_vertex_model(version_id="5")

    with patch.object(model_loader.aiplatform, "init") as mock_init, \
         patch.object(
             model_loader.aiplatform, "Model", return_value=fake_model
         ) as mock_ctor, \
         patch.object(model_loader.aiplatform.Model, "list") as mock_list:
        for p in _patch_heavy_loaders():
            p.start()
        try:
            loaded = load_model()
        finally:
            patch.stopall()

    assert isinstance(loaded, LoadedModel)
    assert loaded.model_version == "5"
    assert loaded.model_run_id == "run-20260419-140149"

    # Env override must use the constructor path, not Model.list.
    mock_ctor.assert_called_once()
    called_kwargs = mock_ctor.call_args.kwargs
    called_args = mock_ctor.call_args.args
    resource = called_kwargs.get("model_name") or (called_args[0] if called_args else None)
    assert resource is not None
    assert "distilbert-priority" in resource
    assert "5" in resource
    mock_list.assert_not_called()
    mock_init.assert_called()


def test_load_model_default_version_fallback():
    non_default = _make_fake_vertex_model(
        version_id="1",
        aliases=(),
        is_default=False,
    )
    default = _make_fake_vertex_model(
        version_id="2",
        aliases=("default",),
        is_default=True,
    )

    with patch.object(model_loader.aiplatform, "init"), \
         patch.object(
             model_loader.aiplatform.Model,
             "list",
             return_value=[non_default, default],
         ) as mock_list:
        for p in _patch_heavy_loaders():
            p.start()
        try:
            loaded = load_model()
        finally:
            patch.stopall()

    assert loaded.model_version == "2"
    assert loaded.model_run_id == "run-20260419-140149"

    mock_list.assert_called_once()
    kwargs = mock_list.call_args.kwargs
    assert 'display_name="distilbert-priority"' in kwargs.get("filter", "")
    assert kwargs.get("order_by") == "create_time desc"


def test_load_model_singleton_cached():
    fake_model = _make_fake_vertex_model(version_id="2")

    with patch.object(model_loader.aiplatform, "init"), \
         patch.object(
             model_loader.aiplatform.Model,
             "list",
             return_value=[fake_model],
         ) as mock_list:
        for p in _patch_heavy_loaders():
            p.start()
        try:
            first = load_model()
            second = load_model()
        finally:
            patch.stopall()

    assert first is second
    # Resolution path ran exactly once.
    assert mock_list.call_count == 1


def test_model_run_id_extraction_via_load(monkeypatch):
    uri = (
        "gs://bucket/models/distilbert-priority/runs/"
        "run-20260419-140149/model/"
    )
    fake = _make_fake_vertex_model(version_id="2", artifact_uri=uri)

    with patch.object(model_loader.aiplatform, "init"), \
         patch.object(
             model_loader.aiplatform.Model, "list", return_value=[fake]
         ):
        for p in _patch_heavy_loaders():
            p.start()
        try:
            loaded = load_model()
        finally:
            patch.stopall()

    assert loaded.model_run_id == "run-20260419-140149"
