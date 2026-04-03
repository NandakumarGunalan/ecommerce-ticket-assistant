from fastapi import FastAPI
from app.predictor import load_model, predict_ticket
from app.schemas import TicketRequest, TicketPredictionResponse

app = FastAPI(
    title="Ecommerce Ticket Assistant API",
    version="0.1.0"
)


@app.on_event("startup")
def startup_event():
    load_model()


@app.get("/")
def root():
    return {
        "message": "Welcome to the Ecommerce Ticket Assistant API"
    }


@app.get("/health")
def health():
    from app.predictor import MODEL
    return {
        "status": "ok",
        "model_loaded": MODEL is not None
    }


@app.post("/predict", response_model=TicketPredictionResponse)
def predict(request: TicketRequest):
    result = predict_ticket(request.text)
    return TicketPredictionResponse(**result)