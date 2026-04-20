"""
Step 2: Generate ticket_text for each row in data/grid.csv using the Gemini API.

Reads data/grid.csv (11,340 skeleton rows), generates ticket_text in batches of 5,
and saves incrementally to data/tickets_raw.csv. Supports resuming — rows that
already have ticket_text in tickets_raw.csv are skipped.

Usage:
    python generate.py [--dry-run] [--limit N] [--workers N]

Prerequisites:
    gcloud auth application-default login
    GCP_PROJECT set in .env
"""

import concurrent.futures
import json
import os
import re
import threading
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_SIZE: int = 5
MAX_RETRIES: int = 2

SYSTEM_PROMPT: str = "You are simulating a customer contacting e-commerce support."

SINGLE_TICKET_TEMPLATE = """\
Write a single customer support message of 3-5 sentences (75-150 words) with these characteristics:
- Issue area: {issue_area}
- Issue category: {issue_category}
- Sub-category: {issue_sub_category}
- Product: {product_sub_category} ({product_category})
- Customer sentiment: {customer_sentiment}
- Issue complexity: {issue_complexity}

Write only the customer's message. Do not include agent replies, greetings from the agent, or any metadata.
The priority signal (urgency, frustration, complexity) should be clear from the language itself — not stated explicitly."""

BATCH_PROMPT_WRAPPER = """\
Given the following {n} customer support scenarios, write one ticket message (3-5 sentences, 75-150 words) for each.
Return a JSON array of exactly {n} strings in the same order. No other text outside the JSON array.

Scenarios:
{numbered_scenarios}"""

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent / "data"
GRID_CSV = DATA_DIR / "grid.csv"
TICKETS_RAW_CSV = DATA_DIR / "tickets_raw.csv"


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_single_scenario(row: dict) -> str:
    """Build the per-scenario block used inside a batch prompt."""
    return SINGLE_TICKET_TEMPLATE.format(
        issue_area=row["issue_area"],
        issue_category=row["issue_category"],
        issue_sub_category=row["issue_sub_category"],
        product_sub_category=row["product_sub_category"],
        product_category=row["product_category"],
        customer_sentiment=row["customer_sentiment"],
        issue_complexity=row["issue_complexity"],
    )


def build_batch_prompt(rows: list[dict]) -> str:
    """Build the batch prompt for a list of row dicts."""
    numbered_scenarios = "\n".join(
        f"{i}. {build_single_scenario(row)}"
        for i, row in enumerate(rows, 1)
    )
    return BATCH_PROMPT_WRAPPER.format(
        n=len(rows),
        numbered_scenarios=numbered_scenarios,
    )


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def extract_json_array(raw: str) -> list[str] | None:
    """Extract the first JSON array from the response, even if wrapped in markdown fences."""
    # Strip markdown code fences if present
    clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean.strip())
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return parsed
    except json.JSONDecodeError:
        pass
    # Fallback: find array anywhere in the string
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------

def call_gemini(client: genai.Client, model_name: str, prompt: str) -> str:
    """Call the Gemini API and return the raw text response."""
    response = client.models.generate_content(
        model=model_name,
        contents=SYSTEM_PROMPT + "\n\n" + prompt,
        config=types.GenerateContentConfig(
            temperature=0.9,
            max_output_tokens=4096,
            thinking_config=types.ThinkingConfig(thinking_budget=128),
        ),
    )
    return response.text.strip()


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def process_batch(
    client: genai.Client,
    model_name: str,
    batch_rows: list[dict],
) -> list[str | None]:
    """
    Generate ticket_text for a batch of rows.

    Returns a list of strings (same length as batch_rows). On complete failure,
    returns a list of None values so the caller can skip and continue.
    """
    prompt = build_batch_prompt(batch_rows)
    expected = len(batch_rows)

    for attempt in range(1, MAX_RETRIES + 2):  # attempts: 1, 2, 3
        try:
            raw = call_gemini(client, model_name, prompt)
        except Exception as exc:
            is_rate_limit = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
            print(f"    API error on attempt {attempt}: {exc}")
            if attempt <= MAX_RETRIES:
                backoff = 30 * attempt if is_rate_limit else 5 * attempt
                print(f"    Retrying in {backoff}s...")
                time.sleep(backoff)
                continue
            return [None] * expected

        texts = extract_json_array(raw)

        if texts is None:
            print(f"    JSON parse failure on attempt {attempt}. Raw (first 200): {raw[:200]!r}")
        elif len(texts) != expected:
            print(f"    Count mismatch on attempt {attempt}: expected {expected}, got {len(texts)}")
            texts = None  # force retry

        if texts is not None:
            return texts

        if attempt <= MAX_RETRIES:
            time.sleep(3 * attempt)

    # All retries exhausted
    return [None] * expected


# ---------------------------------------------------------------------------
# Resume support
# ---------------------------------------------------------------------------

def load_existing_tickets(path: Path) -> dict[int, str]:
    """
    Load already-generated ticket_text values keyed by ticket_id.
    Returns an empty dict if the file does not exist.
    """
    if not path.exists():
        return {}
    try:
        existing = pd.read_csv(path, usecols=["ticket_id", "ticket_text"])
        filled = existing.dropna(subset=["ticket_text"])
        filled = filled[filled["ticket_text"].str.strip() != ""]
        return dict(zip(filled["ticket_id"].astype(int), filled["ticket_text"]))
    except Exception as exc:
        print(f"Warning: could not read existing {path}: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate ticket_text for grid.csv using Gemini")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build prompts and print the first batch without making API calls")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N rows (for testing)")
    parser.add_argument("--workers", type=int, default=5,
                        help="Number of concurrent workers for batch processing (default: 5)")
    parser.add_argument("--project", default=None, help="GCP project ID (overrides .env)")
    parser.add_argument("--location", default=None, help="Vertex AI region (overrides .env)")
    parser.add_argument("--model", default=None, help="Gemini model name (overrides .env)")
    args = parser.parse_args()

    project = args.project or os.environ.get("GCP_PROJECT")
    location = args.location or os.environ.get("GCP_LOCATION", "us-central1")
    model_name = args.model or os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")

    if not args.dry_run:
        if not project or project == "YOUR_PROJECT_ID_HERE":
            raise SystemExit("ERROR: Set GCP_PROJECT in .env before running.")
        print(f"Initializing Vertex AI — project={project}, location={location}")
        print(f"Model: {model_name}")
        client = genai.Client(vertexai=True, project=project, location=location)
    else:
        client = None
        print("DRY RUN — no API calls will be made.")

    # ------------------------------------------------------------------
    # Load grid
    # ------------------------------------------------------------------
    print(f"Reading grid from {GRID_CSV} ...")
    grid = pd.read_csv(GRID_CSV)
    print(f"Grid loaded: {len(grid)} rows")

    if args.limit:
        grid = grid.head(args.limit)
        print(f"Limiting to {len(grid)} rows (--limit {args.limit})")

    # ------------------------------------------------------------------
    # Resume: load already-generated tickets
    # ------------------------------------------------------------------
    existing = load_existing_tickets(TICKETS_RAW_CSV)
    print(f"Already generated: {len(existing)} rows with ticket_text")

    # Identify rows that still need generation
    todo_mask = ~grid["ticket_id"].astype(int).isin(existing.keys())
    todo = grid[todo_mask].copy()
    print(f"Rows to generate: {len(todo)}")

    if len(todo) == 0:
        print("Nothing to do — all rows already have ticket_text.")
        _print_summary(grid, existing)
        return

    # ------------------------------------------------------------------
    # Set up output: either load existing or create a fresh output df
    # ------------------------------------------------------------------
    if TICKETS_RAW_CSV.exists() and existing:
        out_df = pd.read_csv(TICKETS_RAW_CSV)
        # Ensure ticket_text column exists
        if "ticket_text" not in out_df.columns:
            out_df.insert(1, "ticket_text", None)
    else:
        out_df = grid.copy()
        out_df.insert(1, "ticket_text", None)

    # Ensure correct column order: ticket_id, ticket_text, then rest
    cols = list(out_df.columns)
    if cols[0] != "ticket_id" or cols[1] != "ticket_text":
        rest = [c for c in cols if c not in ("ticket_id", "ticket_text")]
        out_df = out_df[["ticket_id", "ticket_text"] + rest]

    # ------------------------------------------------------------------
    # Chunking and generation
    # ------------------------------------------------------------------
    todo_rows = todo.to_dict("records")
    batches = [todo_rows[i:i + BATCH_SIZE] for i in range(0, len(todo_rows), BATCH_SIZE)]
    total_batches = len(batches)
    total_rows = len(todo_rows)

    if args.dry_run:
        print(f"\nDRY RUN: would process {total_rows} rows in {total_batches} batches.")
        print("\n--- First batch prompt preview ---")
        print(build_batch_prompt(batches[0][:BATCH_SIZE]))
        return

    rows_done = 0
    rows_failed = 0
    save_lock = threading.Lock()

    def _worker(batch_idx: int, batch: list[dict]):
        """Process a batch and handle results inline (runs in worker thread)."""
        nonlocal rows_done, rows_failed
        texts = process_batch(client, model_name, batch)

        with save_lock:
            for row, text in zip(batch, texts):
                tid = int(row["ticket_id"])
                if text is not None:
                    out_df.loc[out_df["ticket_id"] == tid, "ticket_text"] = text
                    rows_done += 1
                else:
                    rows_failed += 1

            out_df.to_csv(TICKETS_RAW_CSV, index=False)

            completed_total = len(existing) + rows_done
            print(
                f"Batch {batch_idx}/{total_batches} — "
                f"{completed_total}/{len(grid) if not args.limit else args.limit} rows done"
                + (f" ({rows_failed} failed)" if rows_failed else "")
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = []
        for batch_idx, batch in enumerate(batches, 1):
            futures.append(executor.submit(_worker, batch_idx, batch))
            # Stagger initial submissions to avoid burst
            if batch_idx <= args.workers:
                time.sleep(1.0)

        # Wait for all to complete; exceptions will be re-raised here
        for future in concurrent.futures.as_completed(futures):
            future.result()

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    _print_summary(out_df, load_existing_tickets(TICKETS_RAW_CSV))


def _print_summary(df_or_grid, existing: dict):
    """Print a generation summary."""
    if isinstance(df_or_grid, pd.DataFrame):
        total = len(df_or_grid)
    else:
        total = len(df_or_grid)

    filled = len(existing)
    missing = total - filled

    print(f"\n{'='*50}")
    print("  GENERATION SUMMARY")
    print(f"{'='*50}")
    print(f"  Total rows:        {total}")
    print(f"  Rows with text:    {filled}")
    print(f"  Rows missing text: {missing}")
    if total > 0:
        print(f"  Completion:        {filled / total:.1%}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
