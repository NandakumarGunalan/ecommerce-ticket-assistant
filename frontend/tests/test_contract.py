"""Lightweight contract check for the frontend <-> backend API.

Run with: .venv/bin/python frontend/tests/test_contract.py

These tests do not start a browser. They just load frontend/app.js as text
and assert that it speaks the backend's documented contract:
  - hits GET /health and POST /tickets and GET /tickets and POST /feedback
  - sends {ticket_text: ...} (not {text: ...}) for ticket creation
  - sends {prediction_id, verdict} for feedback
  - does NOT use localStorage for the tickets view
  - does NOT reference the invented "category" field from /predict
"""

from __future__ import annotations

import pathlib
import re
import sys


HERE = pathlib.Path(__file__).resolve().parent
APP_JS = HERE.parent / "app.js"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def main() -> None:
    src = APP_JS.read_text()

    checks = [
        ("GET /health call present",
         r"/health"),
        ("POST /tickets call present",
         r"/tickets`"),
        ("GET /tickets?limit= call present",
         r"/tickets\?limit="),
        ("POST /feedback call present",
         r"/feedback`"),
        ("sends ticket_text field",
         r"ticket_text"),
        ("sends prediction_id + verdict fields",
         r"prediction_id.*verdict|verdict.*prediction_id"),
        ("reads predicted_priority from response",
         r"predicted_priority"),
    ]

    for label, pattern in checks:
        if not re.search(pattern, src, flags=re.DOTALL):
            fail(f"{label}: pattern {pattern!r} not found in app.js")
        print(f"ok: {label}")

    forbidden = [
        ("localStorage usage for tickets", r"localStorage"),
        ("invented 'category' field",
         r"\.category\b|\bcategory_value\b|\"category\""),
        ("stale POST body field 'text'",
         r'JSON\.stringify\(\s*\{\s*text\s*:'),
    ]
    for label, pattern in forbidden:
        if re.search(pattern, src):
            fail(f"{label}: pattern {pattern!r} should not appear in app.js")
        print(f"ok: no {label}")

    print("\nAll contract checks passed.")


if __name__ == "__main__":
    main()
