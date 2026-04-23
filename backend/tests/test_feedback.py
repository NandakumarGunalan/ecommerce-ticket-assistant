"""Tests for ``POST /feedback``."""
from __future__ import annotations

import json
import uuid


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
