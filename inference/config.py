import os

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MODEL_NAME = os.getenv("MODEL_NAME", "ecommerce-ticket-model")
MODEL_STAGE = os.getenv("MODEL_STAGE", "Production")