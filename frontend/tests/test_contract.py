"""Lightweight contract check for the frontend <-> backend API.

Run with: .venv/bin/python frontend/tests/test_contract.py

These tests do not start a browser. They just load the frontend source files
as text and assert that they speak the backend's documented contract:
  - hits GET /health and POST /tickets and GET /tickets and POST /feedback
  - sends {ticket_text: ...} (not {text: ...}) for ticket creation
  - sends {prediction_id, verdict} for feedback
  - does NOT use localStorage for the tickets view
  - does NOT reference the invented "category" field from /predict
  - Wave 4: every real-mode backend call goes through authedFetch (not bare
    fetch to the API base URL), FIREBASE_CONFIG exists in config.js, and
    index.html imports firebase-auth from gstatic.
"""

from __future__ import annotations

import pathlib
import re
import sys


HERE = pathlib.Path(__file__).resolve().parent
FRONTEND = HERE.parent
APP_JS = FRONTEND / "app.js"
CONFIG_JS = FRONTEND / "config.js"
INDEX_HTML = FRONTEND / "index.html"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def main() -> None:
    src = APP_JS.read_text()
    cfg_src = CONFIG_JS.read_text()
    idx_src = INDEX_HTML.read_text()

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
        ("authedFetch helper defined",
         r"async function authedFetch\("),
        ("authedFetch attaches Bearer token",
         r"Authorization.*Bearer"),
        ("calls GET /me to confirm session",
         r"/me"),
        ("handles 401 by signing out",
         r"status === 401"),
        ("handles 429 rate limit",
         r"status === 429"),
        ("apiResolveTicket helper defined",
         r"async function apiResolveTicket\("),
        ("apiUnresolveTicket helper defined",
         r"async function apiUnresolveTicket\("),
        ("include_resolved query param in /tickets fetch",
         r"/tickets\?limit=[^`]*include_resolved="),
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

    # Every backend call in real mode must go through authedFetch (not bare
    # fetch(apiBaseUrl). The one exception is /health which is public. Scan
    # all `fetch(...apiBaseUrl...` calls and assert none target protected
    # routes.
    bare_calls = re.findall(
        r"\bfetch\s*\(\s*`\$\{apiBaseUrl\}([^`]*)`",
        src,
    )
    protected_hits = [p for p in bare_calls if not p.startswith("/health")]
    if protected_hits:
        fail(
            "bare fetch(apiBaseUrl...) found for protected routes "
            f"(should use authedFetch): {protected_hits}"
        )
    print("ok: no bare fetch to protected backend routes")

    # config.js must declare FIREBASE_CONFIG with the expected keys.
    if "FIREBASE_CONFIG" not in cfg_src:
        fail("FIREBASE_CONFIG object missing from config.js")
    for key in ("apiKey", "authDomain", "projectId", "appId"):
        if key not in cfg_src:
            fail(f"FIREBASE_CONFIG missing key {key!r} in config.js")
    print("ok: config.js declares FIREBASE_CONFIG with required keys")

    # index.html must load firebase-auth from gstatic.
    if not re.search(r"gstatic\.com/firebasejs/[^\"']*firebase-auth", idx_src):
        fail("index.html does not import firebase-auth from gstatic")
    if not re.search(r"gstatic\.com/firebasejs/[^\"']*firebase-app", idx_src):
        fail("index.html does not import firebase-app from gstatic")
    print("ok: index.html imports firebase-app + firebase-auth from gstatic")

    # Show-resolved toggle in the Tickets view header.
    if "show-resolved-toggle" not in idx_src:
        fail("index.html missing #show-resolved-toggle checkbox")
    print("ok: index.html has #show-resolved-toggle")

    # Sign-in / sign-out UI wiring.
    for needle in ("btn-sign-in", "btn-sign-out", "auth-gate", "app-main"):
        if needle not in idx_src:
            fail(f"index.html missing required element id #{needle}")
    print("ok: index.html has auth-gate, app-main, sign-in, sign-out wiring")

    # E2E latency display on the prediction card.
    if "latency-value" not in idx_src:
        fail("index.html missing #latency-value (prediction latency display)")
    if "performance.now()" not in src or "__totalMs" not in src:
        fail("app.js missing performance.now() timer around apiCreateTicket")
    print("ok: latency display wired (performance.now -> __totalMs -> #latency-value)")

    print("\nAll contract checks passed.")


if __name__ == "__main__":
    main()
