"""Tests for ``POST /feedback``."""
from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

from backend.api.auth import User, current_user_dep
from backend.api.main import app, get_db, get_model_client


def _make_ticket(client) -> dict:
    r = client.post("/tickets", json={"ticket_text": "help"})
    assert r.status_code == 200
    return r.json()


def test_feedback_happy_path(client, capfd):
    t = _make_ticket(client)
    capfd.readouterr()  # clear the ticket_created line

    r = client.post(
        "/feedback",
        json={
            "prediction_id": t["prediction_id"],
            "verdict": "thumbs_up",
            "note": "nailed it",
        },
    )
    assert r.status_code == 200
    body = r.json()
    uuid.UUID(body["feedback_id"])
    assert "created_at" in body

    captured = capfd.readouterr().out
    events = [
        json.loads(line)
        for line in captured.splitlines()
        if line.strip().startswith("{") and '"feedback_recorded"' in line
    ]
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "feedback_recorded"
    assert ev["prediction_id"] == t["prediction_id"]
    assert ev["verdict"] == "thumbs_up"
    # enriched fields from get_prediction_context
    assert ev["ticket_id"] == t["ticket_id"]
    assert ev["predicted_priority"] == "urgent"


def test_feedback_thumbs_down(client):
    t = _make_ticket(client)
    r = client.post(
        "/feedback",
        json={"prediction_id": t["prediction_id"], "verdict": "thumbs_down"},
    )
    assert r.status_code == 200


def test_feedback_invalid_verdict(client):
    t = _make_ticket(client)
    r = client.post(
        "/feedback",
        json={"prediction_id": t["prediction_id"], "verdict": "meh"},
    )
    assert r.status_code in (400, 422)


def test_feedback_missing_verdict(client):
    t = _make_ticket(client)
    r = client.post(
        "/feedback", json={"prediction_id": t["prediction_id"]}
    )
    assert r.status_code in (400, 422)


def test_feedback_unknown_prediction_404(client):
    r = client.post(
        "/feedback",
        json={"prediction_id": str(uuid.uuid4()), "verdict": "thumbs_up"},
    )
    assert r.status_code == 404


def test_feedback_cross_user_is_404(db, model_stub):
    """User B cannot leave feedback on User A's prediction."""
    user_a = User(uid="user-a", email="a@example.com", display_name="A")
    user_b = User(uid="user-b", email="b@example.com", display_name="B")

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_model_client] = lambda: model_stub
    try:
        client = TestClient(app)

        # User A creates a ticket → prediction.
        app.dependency_overrides[current_user_dep] = lambda: user_a
        r = client.post("/tickets", json={"ticket_text": "A's issue"})
        assert r.status_code == 200
        prediction_id = r.json()["prediction_id"]

        # User B tries to leave feedback on it — 404 (collapsed from
        # "exists but not yours").
        app.dependency_overrides[current_user_dep] = lambda: user_b
        r = client.post(
            "/feedback",
            json={"prediction_id": prediction_id, "verdict": "thumbs_up"},
        )
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()
