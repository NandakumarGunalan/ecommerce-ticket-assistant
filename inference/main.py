"""FastAPI app for online prediction mode.

Online mode is not wired up in this branch — it will be enabled in a
follow-up once the batch pipeline is stable. See inference/PLAN.md,
"Online Mode (Future)" section.
"""
from fastapi import FastAPI

app = FastAPI(
    title="Ecommerce Ticket Assistant — Inference API",
    version="0.1.0",
)


@app.get("/")
def root():
    return {"status": "scaffold", "message": "Online mode not yet implemented."}


@app.get("/health")
def health():
    return {"status": "ok"}


# TODO(online): wire /predict endpoint against inference.predictor.predict
# and inference.schemas when ready.
