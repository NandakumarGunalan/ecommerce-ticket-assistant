from app.model_loader import get_model
import pandas as pd


def predict_ticket(text: str):
    model = get_model()

    if model is None:
        return {
            "priority": "unknown",
            "category": "unknown",
            "confidence": 0.0,
            "model_version": "model_not_loaded"
        }

    df = pd.DataFrame({"text": [text]})
    prediction = model.predict(df)

    predicted_priority = prediction[0]

    return {
        "priority": str(predicted_priority),
        "category": "predicted_category",
        "confidence": 0.90,
        "model_version": "mlflow-model"
    }