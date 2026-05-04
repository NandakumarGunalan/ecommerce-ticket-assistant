"""Tests for ``POST /tickets/upload-csv``."""
from __future__ import annotations

import io


def _csv_file(content: str, filename: str = "tickets.csv"):
    return {"file": (filename, io.BytesIO(content.encode()), "text/csv")}


def test_upload_csv_basic(client, db, stub_user):
    csv = "text\nMy order never arrived\nI need a refund please\n"
    r = client.post("/tickets/upload-csv", files=_csv_file(csv))
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 2
    assert body["skipped"] == 0

    # Tickets appear in the DB with source=csv and no prediction.
    listed = db.list_tickets(user_id=stub_user.uid)
    assert len(listed) == 2
    sources = {db._tickets[t.ticket_id]["source"] for t in listed}
    assert sources == {"csv"}
    assert all(t.prediction_id is None for t in listed)


def test_upload_csv_alternative_column_names(client, db, stub_user):
    for header in ("message", "description", "ticket_text"):
        csv = f"{header}\nThis is a test ticket for {header}\n"
        r = client.post("/tickets/upload-csv", files=_csv_file(csv))
        assert r.status_code == 200
        assert r.json()["accepted"] == 1


def test_upload_csv_fallback_to_first_column(client, db, stub_user):
    csv = "unknown_col\nSome ticket text here\n"
    r = client.post("/tickets/upload-csv", files=_csv_file(csv))
    assert r.status_code == 200
    assert r.json()["accepted"] == 1


def test_upload_csv_skips_short_rows(client, db, stub_user):
    csv = "text\nok\nThis is a valid ticket message\n\n   \n"
    r = client.post("/tickets/upload-csv", files=_csv_file(csv))
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 1
    assert body["skipped"] == 0  # short rows are dropped before counting


def test_upload_csv_respects_500_row_limit(client, db, stub_user):
    rows = "\n".join(f"This is ticket number {i} with enough text" for i in range(600))
    csv = f"text\n{rows}\n"
    r = client.post("/tickets/upload-csv", files=_csv_file(csv))
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 500
    assert body["skipped"] == 100


def test_upload_csv_empty_file(client, db, stub_user):
    r = client.post("/tickets/upload-csv", files=_csv_file("text\n"))
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 0
    assert body["skipped"] == 0


def test_upload_csv_requires_auth(db, model_stub):
    from fastapi.testclient import TestClient
    from backend.api.main import app, get_db, get_model_client

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_model_client] = lambda: model_stub
    try:
        c = TestClient(app)
        r = c.post("/tickets/upload-csv", files=_csv_file("text\nsome ticket\n"))
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_upload_csv_bom_utf8(client, db, stub_user):
    # UTF-8 BOM is common from Excel CSV exports.
    content = "\xef\xbb\xbftext\nThis ticket came from Excel\n"
    r = client.post(
        "/tickets/upload-csv",
        files={"file": ("tickets.csv", io.BytesIO(content.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["accepted"] == 1
