import mlflow
import mlflow.sklearn

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

import pandas as pd

# Set MLflow tracking (IMPORTANT)
mlflow.set_tracking_uri("http://127.0.0.1:5000")
mlflow.set_experiment("ecommerce-ticket-assistant")

# Dummy dataset (for demo)
data = [
    ("I want a refund, I was charged twice", "high"),
    ("My package is delayed", "medium"),
    ("Cannot login to my account", "medium"),
    ("Just asking about product details", "low")
]

df = pd.DataFrame(data, columns=["text", "label"])

# Vectorize
vectorizer = TfidfVectorizer()
X = vectorizer.fit_transform(df["text"])
y = df["label"]

# Train model
model = LogisticRegression()
model.fit(X, y)

# Wrap pipeline
from sklearn.pipeline import Pipeline

pipeline = Pipeline([
    ("tfidf", vectorizer),
    ("clf", model)
])

# MLflow logging
with mlflow.start_run():
    mlflow.sklearn.log_model(pipeline, "model")