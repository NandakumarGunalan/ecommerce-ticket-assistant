import os
import traceback

import mlflow
import mlflow.pyfunc

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://34.58.78.78:5000")
MLFLOW_REGISTRY_URI = os.getenv("MLFLOW_REGISTRY_URI", MLFLOW_TRACKING_URI)

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
mlflow.set_registry_uri(MLFLOW_REGISTRY_URI)

MODEL = None


def load_model():
    global MODEL
    MODEL = mlflow.pyfunc.load_model(
        model_uri="models:/ecommerce-ticket-model/3"
    )


def predict_ticket(text: str):
    if MODEL is None:
        return {
            "priority": "unknown",
            "category": "unknown",
            "confidence": 0.0,
            "model_version": "model_not_loaded"
        }

    try:
        prediction = MODEL.predict([text])
        predicted_label = prediction[0]

        print(f"[PREDICT] input={text} output={predicted_label}", flush=True)

        return {
            "priority": str(predicted_label),
            "category": "ticket_classification",
            "confidence": 0.90,
            "model_version": "v3"
        }
    except Exception as e:
        print(f"[PREDICT_ERROR] {e}", flush=True)
        traceback.print_exc()
        raise