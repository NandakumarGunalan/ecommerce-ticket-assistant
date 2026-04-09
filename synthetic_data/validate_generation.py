"""
Validation test: generate 50 tickets using Vertex AI (ADC) at batch sizes 1, 5, and 10.
Evaluates quality across three dimensions for each batch size:
  1. Sentiment/complexity signal — does the text reflect the metadata?
  2. JSON formatting reliability — does the model return parseable output with the right count?
  3. Text diversity — are outputs within a batch repetitive?

Usage:
    python validate_generation.py

Config is read from synthetic_data/.env — set GCP_PROJECT there before running.

Prerequisites:
    gcloud auth application-default login
"""

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Taxonomy — 50 varied scenarios covering the full label space
# ---------------------------------------------------------------------------

SCENARIOS = [
    # (issue_area, issue_category, issue_sub_category, customer_sentiment, issue_complexity, product_category, product_sub_category)
    ("Order", "Delivery issues", "Delivery delay", "frustrated", "high", "Electronics", "Laptop"),
    ("Order", "Returns & refunds", "Return initiation", "negative", "medium", "Electronics", "Smartphone"),
    ("Order", "Delivery issues", "Wrong item delivered", "frustrated", "high", "Appliances", "Washing Machine"),
    ("Warranty", "Warranty terms", "Coverage details", "neutral", "less", "Electronics", "DSLR Camera"),
    ("Warranty", "Claim process", "Claim submission", "negative", "medium", "Appliances", "Refrigerator"),
    ("Login and Account", "Mobile verification", "OTP not received", "frustrated", "medium", "Electronics", "Smartphone"),
    ("Login and Account", "Account reactivation", "Suspended account", "frustrated", "high", "Electronics", "Tablet"),
    ("Shopping", "Product availability", "Out of stock", "neutral", "less", "Clothing", "Men's Shirts"),
    ("Shopping", "Pricing", "Incorrect price displayed", "negative", "medium", "Electronics", "Headphones"),
    ("Cancellations and Returns", "Pickup scheduling", "Pickup not arrived", "frustrated", "high", "Furniture", "Office Chair"),
    ("Order", "Invoicing", "GST invoice missing", "neutral", "less", "Electronics", "Laptop"),
    ("Shipping", "Delivery options", "Express delivery unavailable", "negative", "less", "Beauty & Personal Care", "Skincare"),
    ("Order", "Installation", "Technician no-show", "frustrated", "high", "Appliances", "Oven Toaster Grill"),
    ("Cancellations and Returns", "CoD refunds", "Cash refund not received", "frustrated", "high", "Clothing", "Women's Dresses"),
    ("Cancellations and Returns", "Replacement timelines", "Replacement delayed", "negative", "medium", "Electronics", "Tablet"),
    ("Order", "Delivery issues", "Delivery delay", "neutral", "less", "Furniture", "Bookshelf"),
    ("Warranty", "Warranty terms", "Warranty period query", "neutral", "less", "Appliances", "Water Purifier"),
    ("Shopping", "Account requirements", "Login required to view price", "neutral", "less", "Clothing", "Kids' Shoes"),
    ("Login and Account", "Password reset", "Reset email not received", "negative", "medium", "Electronics", "Laptop"),
    ("Shipping", "Product availability by region", "Item not deliverable to pincode", "negative", "medium", "Appliances", "Refrigerator"),
    ("Order", "Returns & refunds", "Refund amount incorrect", "frustrated", "medium", "Electronics", "Headphones"),
    ("Warranty", "Claim process", "Claim rejected", "frustrated", "high", "Electronics", "DSLR Camera"),
    ("Order", "Delivery issues", "Delivery damaged", "frustrated", "high", "Electronics", "Smartphone"),
    ("Shopping", "Product availability", "Variant out of stock", "neutral", "medium", "Clothing", "Women's Dresses"),
    ("Order", "Installation", "Installation instructions unclear", "negative", "medium", "Appliances", "Washing Machine"),
    ("Login and Account", "Email verification", "Verification link expired", "negative", "less", "Electronics", "Tablet"),
    ("Cancellations and Returns", "Pickup scheduling", "Reschedule pickup", "neutral", "less", "Furniture", "Dining Table"),
    ("Order", "Delivery issues", "Delivery delay", "negative", "high", "Electronics", "Laptop"),
    ("Warranty", "Coverage details", "Accidental damage coverage", "neutral", "medium", "Electronics", "Smartphone"),
    ("Shopping", "Pricing", "Coupon not applied", "negative", "less", "Beauty & Personal Care", "Hair Care"),
    ("Order", "Returns & refunds", "Return window expired", "frustrated", "medium", "Clothing", "Men's Shirts"),
    ("Shipping", "Delivery options", "Scheduled delivery slot", "neutral", "less", "Appliances", "Oven Toaster Grill"),
    ("Login and Account", "Account reactivation", "Account hacked", "frustrated", "high", "Electronics", "Laptop"),
    ("Cancellations and Returns", "Replacement timelines", "Wrong replacement sent", "frustrated", "high", "Electronics", "Headphones"),
    ("Order", "Invoicing", "Invoice name correction", "neutral", "medium", "Electronics", "DSLR Camera"),
    ("Warranty", "Claim process", "In-home service request", "negative", "medium", "Appliances", "Water Purifier"),
    ("Shopping", "Product availability", "Discontinued product", "neutral", "less", "Furniture", "Bookshelf"),
    ("Order", "Delivery issues", "Package lost in transit", "frustrated", "high", "Electronics", "Tablet"),
    ("Login and Account", "Mobile verification", "Phone number change", "negative", "medium", "Electronics", "Smartphone"),
    ("Cancellations and Returns", "CoD refunds", "Refund to wrong account", "frustrated", "high", "Appliances", "Refrigerator"),
    ("Order", "Returns & refunds", "Return pickup completed, refund pending", "negative", "medium", "Clothing", "Kids' Shoes"),
    ("Warranty", "Warranty terms", "Extended warranty purchase", "neutral", "less", "Electronics", "Laptop"),
    ("Shopping", "Pricing", "Price changed after adding to cart", "negative", "medium", "Electronics", "Headphones"),
    ("Shipping", "Product availability by region", "International shipping query", "neutral", "medium", "Beauty & Personal Care", "Grooming"),
    ("Order", "Installation", "Installation fee dispute", "frustrated", "medium", "Appliances", "Washing Machine"),
    ("Login and Account", "Password reset", "Locked out after failed attempts", "frustrated", "high", "Electronics", "Laptop"),
    ("Cancellations and Returns", "Pickup scheduling", "Pickup item condition dispute", "negative", "high", "Electronics", "DSLR Camera"),
    ("Order", "Delivery issues", "Delivery delay", "neutral", "medium", "Furniture", "Dining Table"),
    ("Warranty", "Claim process", "Claim status check", "neutral", "less", "Appliances", "Oven Toaster Grill"),
    ("Shopping", "Account requirements", "Guest checkout not available", "negative", "less", "Clothing", "Women's Dresses"),
]

assert len(SCENARIOS) == 50, f"Expected 50 scenarios, got {len(SCENARIOS)}"

# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are simulating customers contacting e-commerce support. "
    "Write realistic, natural-sounding customer messages. "
    "The priority signal (urgency, frustration, complexity) must be clear from the language itself — never state it explicitly."
)

SINGLE_TICKET_PROMPT = """\
Write a single customer support message of 3-5 sentences (75-150 words) with these characteristics:
- Issue area: {issue_area}
- Issue category: {issue_category}
- Sub-category: {issue_sub_category}
- Product: {product_sub_category} ({product_category})
- Customer sentiment: {customer_sentiment}
- Issue complexity: {issue_complexity}

Write only the customer's message. Do not include agent replies or any metadata."""

BATCH_PROMPT_HEADER = """\
For each of the {n} customer support scenarios below, write one ticket message (3-5 sentences, 75-150 words).
Return a JSON array of exactly {n} strings in the same order. No other text outside the JSON array.

Scenarios:
{scenarios}"""


def build_single_prompt(scenario: tuple) -> str:
    area, cat, sub, sentiment, complexity, prod_cat, prod_sub = scenario
    return SINGLE_TICKET_PROMPT.format(
        issue_area=area,
        issue_category=cat,
        issue_sub_category=sub,
        customer_sentiment=sentiment,
        issue_complexity=complexity,
        product_category=prod_cat,
        product_sub_category=prod_sub,
    )


def build_batch_prompt(scenarios: list[tuple]) -> str:
    lines = []
    for i, (area, cat, sub, sentiment, complexity, prod_cat, prod_sub) in enumerate(scenarios, 1):
        lines.append(
            f"{i}. issue_area={area}, issue_category={cat}, sub_category={sub}, "
            f"sentiment={customer_sentiment_label(sentiment)}, complexity={complexity}, "
            f"product={prod_sub} ({prod_cat})"
        )
    return BATCH_PROMPT_HEADER.format(n=len(scenarios), scenarios="\n".join(lines))


def customer_sentiment_label(s: str) -> str:
    return s  # passthrough; here for clarity


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TicketResult:
    scenario_index: int
    scenario: tuple
    text: str
    word_count: int
    parse_ok: bool  # only relevant for batch


@dataclass
class BatchResult:
    batch_size: int
    batch_index: int
    scenarios: list[tuple]
    texts: list[str]
    parse_ok: bool
    item_count_ok: bool
    raw_response: str
    latency_s: float


# ---------------------------------------------------------------------------
# Gemini call helpers
# ---------------------------------------------------------------------------

def call_single(client: genai.Client, model_name: str, scenario: tuple) -> tuple[str, float]:
    prompt = build_single_prompt(scenario)
    t0 = time.time()
    response = client.models.generate_content(
        model=model_name,
        contents=SYSTEM_PROMPT + "\n\n" + prompt,
        config=types.GenerateContentConfig(temperature=0.9, max_output_tokens=512),
    )
    latency = time.time() - t0
    return response.text.strip(), latency


def call_batch(client: genai.Client, model_name: str, scenarios: list[tuple]) -> tuple[str, float]:
    prompt = build_batch_prompt(scenarios)
    t0 = time.time()
    response = client.models.generate_content(
        model=model_name,
        contents=SYSTEM_PROMPT + "\n\n" + prompt,
        config=types.GenerateContentConfig(temperature=0.9, max_output_tokens=4096),
    )
    latency = time.time() - t0
    return response.text.strip(), latency


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
# Quality checks
# ---------------------------------------------------------------------------

SENTIMENT_KEYWORDS = {
    "frustrated": ["frustrated", "unacceptable", "disgraceful", "extremely", "furious",
                   "outraged", "ridiculous", "terrible", "horrible", "angry", "sick of",
                   "fed up", "demand", "immediately", "appalled"],
    "negative": ["disappointed", "unhappy", "concern", "issue", "problem", "not satisfied",
                 "dissatisfied", "expected better", "unfortunately", "poor", "complaint"],
    "neutral": [],  # neutral is the absence of strong signal; we don't penalize
}

COMPLEXITY_KEYWORDS = {
    "high": ["multiple", "several", "repeatedly", "already tried", "escalate", "urgent",
             "affecting", "been waiting", "weeks", "month", "still not", "again"],
    "medium": ["follow up", "update", "status", "when will", "how long"],
    "less": [],  # simple queries; hard to check positively
}


def check_sentiment_signal(text: str, sentiment: str) -> bool:
    """Returns True if at least one sentiment keyword is present (for frustrated/negative)."""
    if sentiment == "neutral":
        return True  # nothing to check
    keywords = SENTIMENT_KEYWORDS.get(sentiment, [])
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


def check_complexity_signal(text: str, complexity: str) -> bool:
    """Returns True if at least one complexity keyword is present (for high/medium)."""
    if complexity == "less":
        return True  # nothing to check
    keywords = COMPLEXITY_KEYWORDS.get(complexity, [])
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


def word_count(text: str) -> int:
    return len(text.split())


def jaccard_similarity(a: str, b: str) -> float:
    set_a = set(a.lower().split())
    set_b = set(b.lower().split())
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def max_pairwise_similarity(texts: list[str]) -> float:
    """Return the maximum Jaccard similarity between any two texts in the list."""
    if len(texts) < 2:
        return 0.0
    max_sim = 0.0
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            sim = jaccard_similarity(texts[i], texts[j])
            max_sim = max(max_sim, sim)
    return max_sim


# ---------------------------------------------------------------------------
# Run validation for one batch size
# ---------------------------------------------------------------------------

def run_batch_size(client: genai.Client, model_name: str, batch_size: int) -> dict:
    """Run 50 scenarios at the given batch_size and return aggregate metrics."""
    print(f"\n{'='*60}")
    print(f"  BATCH SIZE: {batch_size}  ({50 // batch_size if batch_size > 1 else 50} API calls)")
    print(f"{'='*60}")

    all_texts: list[str] = []
    sentiment_hits = 0
    complexity_hits = 0
    parse_failures = 0
    count_failures = 0
    total_latency = 0.0
    word_counts: list[int] = []
    batch_max_sims: list[float] = []

    if batch_size == 1:
        for i, scenario in enumerate(SCENARIOS):
            text, latency = call_single(client, model_name, scenario)
            total_latency += latency
            wc = word_count(text)
            word_counts.append(wc)
            all_texts.append(text)

            sentiment_ok = check_sentiment_signal(text, scenario[3])
            complexity_ok = check_complexity_signal(text, scenario[4])
            if sentiment_ok:
                sentiment_hits += 1
            if complexity_ok:
                complexity_hits += 1

            status = "OK" if sentiment_ok and complexity_ok else "WARN"
            print(f"  [{i+1:02d}/{len(SCENARIOS)}] {status:4s} | {wc:3d} words | {latency:.1f}s | "
                  f"sentiment={scenario[3]} complexity={scenario[4]}")
    else:
        chunks = [SCENARIOS[i:i+batch_size] for i in range(0, len(SCENARIOS), batch_size)]
        for batch_idx, chunk in enumerate(chunks):
            raw, latency = call_batch(client, model_name, chunk)
            total_latency += latency
            texts = extract_json_array(raw)

            parse_ok = texts is not None
            count_ok = parse_ok and len(texts) == len(chunk)

            if not parse_ok:
                parse_failures += 1
                print(f"  Batch {batch_idx+1}: PARSE FAILURE ({latency:.1f}s)")
                print(f"    Raw (first 200 chars): {raw[:200]!r}")
                # Fill with empty strings so indices stay aligned
                texts = [""] * len(chunk)
            elif not count_ok:
                count_failures += 1
                print(f"  Batch {batch_idx+1}: COUNT MISMATCH — expected {len(chunk)}, got {len(texts)} ({latency:.1f}s)")
                # Pad or truncate to align
                texts = (texts + [""] * len(chunk))[:len(chunk)]
            else:
                sim = max_pairwise_similarity(texts)
                batch_max_sims.append(sim)
                print(f"  Batch {batch_idx+1}: OK | {latency:.1f}s | max_sim={sim:.2f}")

            for i, (scenario, text) in enumerate(zip(chunk, texts)):
                wc = word_count(text)
                word_counts.append(wc)
                all_texts.append(text)
                if text:
                    sentiment_ok = check_sentiment_signal(text, scenario[3])
                    complexity_ok = check_complexity_signal(text, scenario[4])
                    if sentiment_ok:
                        sentiment_hits += 1
                    if complexity_ok:
                        complexity_hits += 1

    # Aggregate stats
    valid_texts = [t for t in all_texts if t]
    n = len(SCENARIOS)
    stats = {
        "batch_size": batch_size,
        "total_scenarios": n,
        "total_api_calls": n if batch_size == 1 else (n // batch_size + (1 if n % batch_size else 0)),
        "parse_failures": parse_failures,
        "count_failures": count_failures,
        "sentiment_signal_rate": sentiment_hits / n,
        "complexity_signal_rate": complexity_hits / n,
        "avg_word_count": sum(word_counts) / len(word_counts) if word_counts else 0,
        "min_word_count": min(word_counts) if word_counts else 0,
        "max_word_count": max(word_counts) if word_counts else 0,
        "avg_max_batch_similarity": sum(batch_max_sims) / len(batch_max_sims) if batch_max_sims else 0,
        "total_latency_s": total_latency,
        "avg_latency_per_call_s": total_latency / (n if batch_size == 1 else len([SCENARIOS[i:i+batch_size] for i in range(0, n, batch_size)])),
    }

    print(f"\n  --- Summary for batch_size={batch_size} ---")
    print(f"  Parse failures:          {parse_failures}")
    print(f"  Count mismatches:        {count_failures}")
    print(f"  Sentiment signal rate:   {stats['sentiment_signal_rate']:.0%}")
    print(f"  Complexity signal rate:  {stats['complexity_signal_rate']:.0%}")
    print(f"  Avg word count:          {stats['avg_word_count']:.0f} (range {stats['min_word_count']}–{stats['max_word_count']})")
    if batch_max_sims:
        print(f"  Avg max intra-batch sim: {stats['avg_max_batch_similarity']:.2f}  (lower = more diverse)")
    print(f"  Total latency:           {total_latency:.1f}s")
    print(f"  Avg latency / API call:  {stats['avg_latency_per_call_s']:.1f}s")

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Validate Gemini generation quality at batch sizes 1, 5, 10")
    parser.add_argument("--project", default=None, help="GCP project ID (overrides .env)")
    parser.add_argument("--location", default=None, help="Vertex AI region (overrides .env)")
    parser.add_argument("--model", default=None, help="Gemini model name (overrides .env)")
    parser.add_argument("--batch-sizes", nargs="+", type=int, default=[1, 5, 10],
                        help="Batch sizes to test (default: 1 5 10)")
    args = parser.parse_args()

    project = args.project or os.environ.get("GCP_PROJECT")
    location = args.location or os.environ.get("GCP_LOCATION", "us-central1")
    model_name = args.model or os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")

    if not project or project == "YOUR_PROJECT_ID_HERE":
        raise SystemExit("ERROR: Set GCP_PROJECT in synthetic_data/.env before running.")

    print(f"Initializing Vertex AI — project={project}, location={location}")
    print("Using Application Default Credentials (ADC)")
    print(f"Model: {model_name}")
    client = genai.Client(vertexai=True, project=project, location=location)

    all_stats = []
    for batch_size in args.batch_sizes:
        stats = run_batch_size(client, model_name, batch_size)
        all_stats.append(stats)
        if batch_size != args.batch_sizes[-1]:
            print("\nPausing 5s between batch sizes...")
            time.sleep(5)

    print(f"\n{'='*60}")
    print("  FINAL COMPARISON")
    print(f"{'='*60}")
    header = f"{'Metric':<35} " + "  ".join(f"batch={s['batch_size']:>2}" for s in all_stats)
    print(header)
    print("-" * len(header))

    rows = [
        ("Parse failures", "parse_failures", "{:.0f}"),
        ("Count mismatches", "count_failures", "{:.0f}"),
        ("Sentiment signal rate", "sentiment_signal_rate", "{:.0%}"),
        ("Complexity signal rate", "complexity_signal_rate", "{:.0%}"),
        ("Avg word count", "avg_word_count", "{:.0f}"),
        ("Avg max intra-batch similarity", "avg_max_batch_similarity", "{:.2f}"),
        ("Total API calls", "total_api_calls", "{:.0f}"),
        ("Total latency (s)", "total_latency_s", "{:.1f}"),
        ("Avg latency / call (s)", "avg_latency_per_call_s", "{:.1f}"),
    ]
    for label, key, fmt in rows:
        values = "  ".join(f"{fmt.format(s[key]):>12}" for s in all_stats)
        print(f"{label:<35} {values}")

    print("\nDone. Use these results to choose your batch size before full generation.")


if __name__ == "__main__":
    main()
