FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TRANSFORMERS_NO_ADVISORY_WARNINGS=1

WORKDIR /app

# System packages: libpq not needed (pg8000 is pure Python).
# ca-certificates for HTTPS to Vertex Registry / GCS.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY inference/requirements.txt /app/inference/requirements.txt
RUN pip install --no-cache-dir -r /app/inference/requirements.txt

COPY inference /app/inference

ENV PYTHONPATH=/app

# Default entrypoint = batch mode. Cloud Run Service deployment overrides CMD
# to run uvicorn for online serving.
ENTRYPOINT ["python", "-m", "inference.batch_predict"]
