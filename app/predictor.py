import mlflow
import mlflow.pyfunc

mlflow.set_tracking_uri("http://127.0.0.1:5001")
mlflow.set_registry_uri("http://127.0.0.1:5001")

MODEL = None


def load_model():
    global MODEL
    MODEL = mlflow.pyfunc.load_model(
        model_uri="models:/ecommerce-ticket-model@champion"
    )


def predict_ticket(text: str):
    if MODEL is None:
        return {
            "priority": "unknown",
            "category": "unknown",
            "confidence": 0.0,
            "model_version": "model_not_loaded"
        }

    prediction = MODEL.predict([text])[0]
    print(f"[PREDICT] input={text} output={prediction}")

    return {
        "priority": str(prediction),
        "category": "ticket_classification",
        "confidence": 0.90,
        "model_version": "v3"
    }