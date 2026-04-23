FROM python:3.11-slim

WORKDIR /app

# Copy static site contents. Build context is the repo root, so we reference
# the frontend/ subdir. The container only needs the static files themselves.
COPY frontend/ /app/

EXPOSE 8080

CMD ["python", "-m", "http.server", "8080"]
