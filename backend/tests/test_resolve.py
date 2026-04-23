"""Tests for ``POST /tickets/{id}/resolve`` / ``/unresolve`` and
``GET /tickets?include_resolved=...``."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.auth import User, current_user_dep
from backend.api.main import app, get_db, get_model_client


def test_resolve_happy_path(client, stub_user):
    r = client.post("/tickets", json={"ticket_text": "please help"})
    assert r.status_code == 200
    ticket_id = r.json()["ticket_id"]
    assert r.json().get("resolved_at") is None

    r2 = client.post(f"/tickets/{ticket_id}/resolve")
    assert r2.status_code == 200
    body = r2.json()
    assert body["ticket_id"] == ticket_id
    assert body["resolved_at"] is not None


def test_resolve_cross_user_returns_404(db, model_stub):
    user_a = User(uid="user-a", email="a@example.com", display_name="A")
    user_b = User(uid="user-b", email="b@example.com", display_name="B")

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_model_client] = lambda: model_stub
    try:
        c = TestClient(app)

        app.dependency_overrides[current_user_dep] = lambda: user_a
        r = c.post("/tickets", json={"ticket_text": "mine"})
        assert r.status_code == 200
        ticket_id = r.json()["ticket_id"]

        # User B tries to resolve A's ticket.
        app.dependency_overrides[current_user_dep] = lambda: user_b
        r = c.post(f"/tickets/{ticket_id}/resolve")
        assert r.status_code == 404

        r = c.post(f"/tickets/{ticket_id}/unresolve")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_unresolve_happy_path(client):
    r = client.post("/tickets", json={"ticket_text": "reopen me"})
    ticket_id = r.json()["ticket_id"]

    r = client.post(f"/tickets/{ticket_id}/resolve")
    assert r.status_code == 200
    assert r.json()["resolved_at"] is not None

    r = client.post(f"/tickets/{ticket_id}/unresolve")
    assert r.status_code == 200
    assert r.json()["resolved_at"] is None


def test_list_default_excludes_resolved(client):
    r1 = client.post("/tickets", json={"ticket_text": "one"})
    r2 = client.post("/tickets", json={"ticket_text": "two"})
    t1 = r1.json()["ticket_id"]
    t2 = r2.json()["ticket_id"]
    assert client.post(f"/tickets/{t1}/resolve").status_code == 200

    r = client.get("/tickets")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["ticket_id"] == t2


def test_list_include_resolved_returns_both(client):
    r1 = client.post("/tickets", json={"ticket_text": "one"})
    r2 = client.post("/tickets", json={"ticket_text": "two"})
    t1 = r1.json()["ticket_id"]
    assert client.post(f"/tickets/{t1}/resolve").status_code == 200

    r = client.get("/tickets?include_resolved=true")
    assert r.status_code == 200
    items = r.json()
    ids = {it["ticket_id"] for it in items}
    assert ids == {t1, r2.json()["ticket_id"]}
    # The resolved one carries a resolved_at value.
    resolved_item = next(it for it in items if it["ticket_id"] == t1)
    assert resolved_item["resolved_at"] is not None


def test_resolve_requires_auth(db, model_stub):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_model_client] = lambda: model_stub
    try:
        c = TestClient(app)
        # Unknown ticket id is fine — unauth is checked before lookup.
        r = c.post("/tickets/00000000-0000-0000-0000-000000000000/resolve")
        assert r.status_code == 401
        r = c.post("/tickets/00000000-0000-0000-0000-000000000000/unresolve")
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_resolve_logs_structured_event(client, capfd):
    import json as _json

    r = client.post("/tickets", json={"ticket_text": "log me"})
    ticket_id = r.json()["ticket_id"]
    capfd.readouterr()  # drain prior output

    assert client.post(f"/tickets/{ticket_id}/resolve").status_code == 200
    out = capfd.readouterr().out
    events = [
        _json.loads(line)
        for line in out.splitlines()
        if line.strip().startswith("{") and '"ticket_resolved"' in line
    ]
    assert len(events) == 1
    assert events[0]["event"] == "ticket_resolved"
    assert events[0]["ticket_id"] == ticket_id

    assert client.post(f"/tickets/{ticket_id}/unresolve").status_code == 200
    out = capfd.readouterr().out
    events = [
        _json.loads(line)
        for line in out.splitlines()
        if line.strip().startswith("{") and '"ticket_unresolved"' in line
    ]
    assert len(events) == 1
    assert events[0]["event"] == "ticket_unresolved"
