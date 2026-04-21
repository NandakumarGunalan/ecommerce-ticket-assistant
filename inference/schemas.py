from pydantic import BaseModel, Field


class TicketRequest(BaseModel):
    text: str = Field(..., min_length=5, description="Customer support ticket text")


class TicketPredictionResponse(BaseModel):
    priority: str
    category: str
    confidence: float
    model_version: str
