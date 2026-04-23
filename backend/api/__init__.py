"""FastAPI backend API for the ticket assistant.

This package hosts the public REST service (`ticket-backend-api` on Cloud
Run) that the frontend calls. It is a thin layer that:

1. Proxies to the model endpoint (`distilbert-priority-online`) for scoring,
   attaching a Google-signed ID token on every request.
2. Persists tickets, predictions, and human feedback to Cloud SQL (Postgres
   instance `ticket-assistant-db`).
3. Emits structured JSON logs for `ticket_created`, `feedback_recorded`,
   and `model_endpoint_error` events so Cloud Logging can surface them.
"""
