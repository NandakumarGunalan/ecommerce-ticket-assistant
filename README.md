# E-commerce Ticket Classification System (MLOps + Deployment)

## Overview

This project demonstrates an end-to-end machine learning system for classifying customer support tickets by priority using a production-style MLOps workflow.

The system includes:
- Model training and experiment tracking with MLflow
- Model versioning and registry
- A FastAPI-based inference service
- Dockerized deployment for reproducibility and portability

The goal is to simulate a real-world ML pipeline where models are trained, registered, and served via a scalable API.

---

## 🏗️ Architecture


User Request → FastAPI (Docker) → MLflow Model Registry → Prediction Response


Key components:
- **MLflow Tracking Server (remote)**: experiment tracking + model registry  
- **Training Pipeline**: logs and registers versioned models  
- **Inference Service (FastAPI)**: loads model from registry at startup  
- **Docker**: ensures consistent runtime environment  

---

## ⚙️ Tech Stack

- Python 3.12  
- scikit-learn  
- MLflow  
- FastAPI  
- Uvicorn  
- Docker  

---

## 📦 Project Structure


ecommerce-ticket-assistant/
│
├── app/
│ ├── main.py # FastAPI app
│ └── predictor.py # Model loading + inference logic
│
├── scripts/
│ └── train_model.py # Training + MLflow logging + registration
│
├── requirements.txt
├── Dockerfile
└── README.md


---

## 🚀 Model Training

The training pipeline:
- Uses a TF-IDF + Logistic Regression pipeline  
- Logs the model to MLflow  
- Registers the model under a versioned name  

Run:

```bash
python scripts/train_model.py
```
This will:

Log a run to MLflow
Register a new version under:
ecommerce-ticket-model
🧠 Model Design

The model is implemented as a scikit-learn Pipeline:

TfidfVectorizer → converts raw text into features
LogisticRegression → predicts ticket priority

This ensures:

Consistent preprocessing between training and inference
Simpler deployment (single serialized artifact)
🔁 Model Registry

The model is loaded in the API using:

mlflow.pyfunc.load_model("models:/ecommerce-ticket-model/<version>")

This allows:

Version-controlled deployment
Easy rollback or promotion of models
Decoupling training from serving
🌐 Inference API
POST /predict

Request:

{
  "text": "My order has not arrived yet"
}

Response:

{
  "priority": "medium",
  "category": "ticket_classification",
  "confidence": 0.9,
  "model_version": "v3"
}
🐳 Docker Deployment

Build image:

docker build -t ecommerce-ticket-assistant .

Run container:

docker run -p 8001:8000 ecommerce-ticket-assistant

Access API:

http://127.0.0.1:8001/docs
🧪 Testing

Example using curl:

curl -X POST "http://127.0.0.1:8001/predict" \
  -H "Content-Type: application/json" \
  -d '{"text":"I want a refund immediately"}'
⚠️ Key Engineering Considerations
1. Training vs Inference Consistency

Initially, the model was logged without preprocessing, causing inference failures.
This was resolved by logging a full Pipeline instead of a raw estimator.

2. Remote MLflow Integration

The API connects to a remote MLflow tracking server, enabling:

centralized experiment tracking
shared model registry across environments
3. Container Networking

Docker was configured to correctly access the host MLflow server via:

host.docker.internal
4. Dependency Management

Model dependency mismatches (e.g., psutil) were identified via MLflow warnings and handled during container build.

📈 Future Improvements
Add larger, realistic dataset
Introduce evaluation metrics and logging
Implement model monitoring (drift detection)
Add CI/CD pipeline for automated retraining and deployment
Deploy to Kubernetes / cloud environment
💡 Summary

This project demonstrates:

practical MLOps concepts
production-ready model serving
debugging across training, registry, and deployment layers

It reflects real-world challenges in bridging the gap between model development and production systems.
