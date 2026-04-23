FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ca-certificates for HTTPS (model endpoint, Secret Manager, Cloud SQL).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Only the API package — no need to ship the training / inference code
# into the backend image.
COPY backend/__init__.py /app/backend/__init__.py
COPY backend/api /app/backend/api

ENV PYTHONPATH=/app

CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
