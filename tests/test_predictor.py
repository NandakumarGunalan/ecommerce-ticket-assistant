from unittest.mock import Mock

import pytest

import app.predictor as predictor_module


def test_load_model_uses_expected_uri_and_sets_model(monkeypatch):
    mocked_loaded_model = Mock()
    mocked_load_model = Mock(return_value=mocked_loaded_model)
    monkeypatch.setattr(predictor_module.mlflow.pyfunc, "load_model", mocked_load_model)

    predictor_module.load_model()

    mocked_load_model.assert_called_once_with(model_uri="models:/ecommerce-ticket-model/3")
    assert predictor_module.MODEL is mocked_loaded_model


def test_predict_ticket_returns_fallback_when_model_not_loaded():
    predictor_module.MODEL = None

    result = predictor_module.predict_ticket("Order has not shipped yet.")

    assert result == {
        "priority": "unknown",
        "category": "unknown",
        "confidence": 0.0,
        "model_version": "model_not_loaded",
    }


def test_predict_ticket_uses_model_prediction(mocked_model):
    predictor_module.MODEL = mocked_model

    result = predictor_module.predict_ticket("Payment failed repeatedly at checkout.")

    assert result == {
        "priority": "high_priority",
        "category": "ticket_classification",
        "confidence": 0.90,
        "model_version": "v3",
    }
    mocked_model.predict.assert_called_once_with(["Payment failed repeatedly at checkout."])


def test_predict_ticket_raises_when_model_prediction_fails(monkeypatch):
    predictor_module.MODEL = Mock()
    predictor_module.MODEL.predict.side_effect = RuntimeError("model predict failure")
    mocked_traceback = Mock()
    monkeypatch.setattr(predictor_module.traceback, "print_exc", mocked_traceback)

    with pytest.raises(RuntimeError, match="model predict failure"):
        predictor_module.predict_ticket("A failing prediction request")

    mocked_traceback.assert_called_once()
