FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV MLFLOW_TRACKING_URI=http://34.58.78.78:5000
ENV MLFLOW_REGISTRY_URI=http://34.58.78.78:5000

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]