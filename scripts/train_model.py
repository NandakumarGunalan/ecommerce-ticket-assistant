import mlflow
import mlflow.sklearn

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

import pandas as pd

# Set MLflow tracking (IMPORTANT)
mlflow.set_tracking_uri("http://34.58.78.78:5000")
mlflow.set_experiment("placeholder-model")

# Dummy dataset (for demo)
data = [
    ("I want a refund, I was charged twice", "high"),
    ("My package is delayed", "medium"),
    ("Cannot login to my account", "medium"),
    ("Just asking about product details", "low")
]

df = pd.DataFrame(data, columns=["text", "label"])

X = df["text"]
y = df["label"]

# Build and fit pipeline
pipeline = Pipeline([
    ("tfidf", TfidfVectorizer()),
    ("clf", LogisticRegression())
])

pipeline.fit(X, y)

# Log + register pipeline model
with mlflow.start_run():
    mlflow.sklearn.log_model(
        sk_model=pipeline,
        artifact_path="model",
        registered_model_name="ecommerce-ticket-model"
    )