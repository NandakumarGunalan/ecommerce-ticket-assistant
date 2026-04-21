import mlflow
import mlflow.pyfunc
from app.config import MLFLOW_TRACKING_URI, MODEL_NAME, MODEL_STAGE

_model = None


def load_model():
    global _model
    try:
        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        model_uri = f"models:/{MODEL_NAME}/{MODEL_STAGE}"
        _model = mlflow.pyfunc.load_model(model_uri)
        print(f"Model loaded successfully from {model_uri}")
    except Exception as e:
        print(f"Failed to load model: {e}")
        _model = None


def get_model():
    return _model
