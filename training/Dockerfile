FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TRANSFORMERS_NO_ADVISORY_WARNINGS=1

WORKDIR /app

COPY training/requirements.txt /app/training/requirements.txt
RUN pip install --no-cache-dir -r /app/training/requirements.txt

COPY training /app/training

ENV PYTHONPATH=/app

ENTRYPOINT ["python", "-m", "training.train"]
