"""Tests for ``POST /tickets`` and ``GET /tickets``."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from backend.api.auth import User, current_user_dep
from backend.api.db_client import InMemoryDBClient
from backend.api.main import app, get_db, get_model_client


def test_create_ticket_persists_both_rows(client, db, model_stub, stub_user):
    r = client.post(
        "/tickets", json={"ticket_text": "my order never arrived"}
    )
    assert r.status_code == 200
    body = r.json()

    # UUIDs come back as strings
    uuid.UUID(body["ticket_id"])
    uuid.UUID(body["prediction_id"])

    assert body["predicted_priority"] == "urgent"
    assert body["confidence"] == 0.83
    assert body["all_scores"]["urgent"] == 0.83
    assert body["model_version"] == "2"
    assert body["latency_ms"] == 42
    assert "created_at" in body
    # Original ticket text should round-trip in the response.
    assert body["text"] == "my order never arrived"

    # DB actually has it, scoped to the stub user.
    listed = db.list_tickets(user_id=stub_user.uid)
    assert len(listed) == 1
    assert listed[0].ticket_id == body["ticket_id"]
    assert listed[0].prediction_id == body["prediction_id"]
    # And the stored ticket row carries the stub user's uid.
    assert db._tickets[body["ticket_id"]]["user_id"] == stub_user.uid


def test_create_ticket_logs_structured_event(client, capfd):
    r = client.post("/tickets", json={"ticket_text": "please help"})
    assert r.status_code == 200
    captured = capfd.readouterr().out
    # There should be one ticket_created JSON line somewhere in stdout.
    events = [
        json.loads(line)
        for line in captured.splitlines()
        if line.strip().startswith("{") and '"ticket_created"' in line
    ]
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "ticket_created"
    assert event["predicted_priority"] == "urgent"
    assert event["confidence"] == 0.83
    assert event["input_preview"] == "please help"
    # Full ticket text must NOT be logged.
    assert "ticket_text" not in event


def test_create_ticket_validation_errors(client):
    for payload in [{"ticket_text": ""}, {"ticket_text": " "}, {}]:
        r = client.post("/tickets", json=payload)
        assert r.status_code in (400, 422)


def test_create_ticket_model_endpoint_error_502(client, model_stub):
    from backend.api.model_client import ModelEndpointError

    model_stub.predict_error = ModelEndpointError(
        "boom", status_code=503
    )
    r = client.post("/tickets", json={"ticket_text": "hi"})
    assert r.status_code == 502


def test_list_tickets_empty(client):
    r = client.get("/tickets")
    assert r.status_code == 200
    assert r.json() == []


def test_list_tickets_sorted_by_priority_then_created_desc(
    client, db, stub_user
):
    # Seed three tickets with different priorities and distinct times.
    base = datetime.now(timezone.utc)
    scenarios = [
        ("low", base - timedelta(minutes=5)),
        ("urgent", base - timedelta(minutes=4)),
        ("medium", base - timedelta(minutes=3)),
        ("urgent", base - timedelta(minutes=1)),  # newest urgent
        ("high", base - timedelta(minutes=2)),
    ]
    # Use the real in-memory insert, then backdate created_at.
    inserted = []
    for priority, ts in scenarios:
        rec = db.insert_ticket_and_prediction(
            user_id=stub_user.uid,
            ticket_text=f"t-{priority}",
            predicted_priority=priority,
            confidence=0.9,
            all_scores={priority: 0.9},
            model_version="2",
            model_run_id="run-test",
            latency_ms=10,
        )
        # Patch timestamps on the underlying dicts so ordering by
        # created_at is deterministic.
        db._tickets[rec.ticket_id]["created_at"] = ts
        db._predictions[rec.prediction_id]["created_at"] = ts
        inserted.append((priority, ts, rec.ticket_id))

    r = client.get("/tickets?limit=10")
    assert r.status_code == 200
    got = r.json()
    priorities = [item["predicted_priority"] for item in got]
    # Every listed ticket must carry its original text through.
    for item in got:
        assert item["text"] == f"t-{item['predicted_priority']}"
    # urgent > high > medium > low; newest urgent first among the two urgents.
    assert priorities == ["urgent", "urgent", "high", "medium", "low"]
    # Within urgents: newest first.
    urgent_items = [i for i in got if i["predicted_priority"] == "urgent"]
    assert (
        datetime.fromisoformat(urgent_items[0]["created_at"].replace("Z", "+00:00"))
        > datetime.fromisoformat(urgent_items[1]["created_at"].replace("Z", "+00:00"))
    )


def test_list_tickets_unknown_priority_sorts_last(client, db, stub_user):
    # Craft a ticket with no prediction to exercise the "unknown" bucket.
    ticket_id = str(uuid.uuid4())
    db._tickets[ticket_id] = {
        "id": ticket_id,
        "user_id": stub_user.uid,
        "text": "orphan",
        "source": "paste",
        "created_at": datetime.now(timezone.utc),
    }
    # And a real scored ticket.
    db.insert_ticket_and_prediction(
        user_id=stub_user.uid,
        ticket_text="scored",
        predicted_priority="low",
        confidence=0.5,
        all_scores={"low": 0.5},
        model_version="2",
        model_run_id=None,
        latency_ms=1,
    )
    r = client.get("/tickets")
    items = r.json()
    assert items[0]["predicted_priority"] == "low"
    assert items[1]["predicted_priority"] == "unknown"
    assert items[1]["all_scores"] == {}


def test_list_tickets_respects_limit(client, db, stub_user):
    for i in range(5):
        db.insert_ticket_and_prediction(
            user_id=stub_user.uid,
            ticket_text=f"t{i}",
            predicted_priority="medium",
            confidence=0.5,
            all_scores={"medium": 0.5},
            model_version="2",
            model_run_id=None,
            latency_ms=1,
        )
    r = client.get("/tickets?limit=2")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_tickets_limit_out_of_range(client):
    r = client.get("/tickets?limit=0")
    assert r.status_code == 422
    r2 = client.get("/tickets?limit=1000")
    assert r2.status_code == 422


def test_list_tickets_is_scoped_to_user(db, model_stub):
    """User B must not see tickets User A created.

    Exercises the endpoint (not just the in-memory fake) by flipping the
    ``current_user_dep`` override between requests.
    """
    from fastapi.testclient import TestClient

    user_a = User(uid="user-a", email="a@example.com", display_name="A")
    user_b = User(uid="user-b", email="b@example.com", display_name="B")

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_model_client] = lambda: model_stub
    try:
        client = TestClient(app)

        app.dependency_overrides[current_user_dep] = lambda: user_a
        r = client.post("/tickets", json={"ticket_text": "A's ticket"})
        assert r.status_code == 200
        a_ticket_id = r.json()["ticket_id"]

        # User A sees their own ticket.
        r = client.get("/tickets")
        assert r.status_code == 200
        a_list = r.json()
        assert [t["ticket_id"] for t in a_list] == [a_ticket_id]

        # User B sees nothing.
        app.dependency_overrides[current_user_dep] = lambda: user_b
        r = client.get("/tickets")
        assert r.status_code == 200
        assert r.json() == []
    finally:
        app.dependency_overrides.clear()


def test_tickets_require_auth(db, model_stub):
    """With no ``current_user_dep`` override, protected routes return 401."""
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_model_client] = lambda: model_stub
    try:
        client = TestClient(app)
        r = client.get("/tickets")
        assert r.status_code == 401
        r = client.post("/tickets", json={"ticket_text": "hi"})
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()
