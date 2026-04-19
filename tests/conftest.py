from pathlib import Path
import sys
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main as main_module
import app.predictor as predictor_module


@pytest.fixture
def sample_payload():
    return {"text": "Customer cannot complete checkout due to payment timeout."}


@pytest.fixture
def mocked_model():
    model = Mock()
    model.predict.return_value = ["high_priority"]
    return model


@pytest.fixture
def client(monkeypatch):
    # Avoid real startup-time model loading (mlflow / network / filesystem).
    monkeypatch.setattr(main_module, "load_model", lambda: None)
    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def reset_model():
    previous = predictor_module.MODEL
    predictor_module.MODEL = None
    try:
        yield
    finally:
        predictor_module.MODEL = previous


@pytest.fixture(autouse=True)
def block_real_mlflow_load(monkeypatch):
    def _unexpected_mlflow_load(*_args, **_kwargs):
        raise AssertionError("Real mlflow.pyfunc.load_model should never be called in unit tests")

    monkeypatch.setattr(predictor_module.mlflow.pyfunc, "load_model", _unexpected_mlflow_load)
