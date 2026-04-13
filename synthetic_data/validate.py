"""
Step 3: Validate and clean data/tickets_raw.csv, saving the result to data/tickets.csv.

Cleaning steps:
  1. Drop rows where ticket_text is NaN, empty, or only whitespace.
  2. Drop rows with exact duplicate ticket_text (keep first).
  3. Drop rows where ticket_text word count < MIN_WORD_COUNT (too short to be useful).

After cleaning, the priority class distribution is checked against TARGET_DISTRIBUTION.
A warning is printed for any class that is more than 5 percentage points off from its target.

Usage:
    python validate.py

Public API (used by tests):
    validate(df)           -> cleaned DataFrame
    check_distribution(df) -> list of warning strings
    TARGET_DISTRIBUTION    -> dict of target class proportions
    MIN_WORD_COUNT         -> int minimum word count threshold
"""

from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_DISTRIBUTION: dict = {
    "low": 0.20,
    "medium": 0.35,
    "high": 0.30,
    "urgent": 0.15,
}

MIN_WORD_COUNT: int = 10

TOLERANCE: float = 0.05  # warn if actual proportion is off by more than 5 pp

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent / "data"
TICKETS_RAW_CSV = DATA_DIR / "tickets_raw.csv"
TICKETS_CSV = DATA_DIR / "tickets.csv"


# ---------------------------------------------------------------------------
# Validation logic
# ---------------------------------------------------------------------------

def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Validate and clean a raw tickets DataFrame.

    Steps:
      1. Drop rows where ticket_text is NaN, empty, or only whitespace.
      2. Drop rows with exact duplicate ticket_text (keep first).
      3. Drop rows where ticket_text word count < MIN_WORD_COUNT.

    Args:
        df: Raw DataFrame read from tickets_raw.csv.

    Returns:
        Cleaned DataFrame with the same columns, reset index.
    """
    initial_count = len(df)
    print(f"Initial row count: {initial_count:,}")

    # ------------------------------------------------------------------
    # Step 1: Drop rows with missing / blank ticket_text
    # ------------------------------------------------------------------
    before = len(df)
    df = df.copy()
    df = df[df["ticket_text"].notna()]
    df = df[df["ticket_text"].str.strip() != ""]
    dropped_blank = before - len(df)
    if dropped_blank:
        print(f"  Dropped {dropped_blank:,} rows with empty or blank ticket_text.")
    else:
        print(f"  No rows dropped for empty/blank ticket_text.")

    # ------------------------------------------------------------------
    # Step 2: Drop exact duplicate ticket_text (keep first occurrence)
    # ------------------------------------------------------------------
    before = len(df)
    df = df.drop_duplicates(subset=["ticket_text"], keep="first")
    dropped_dupes = before - len(df)
    if dropped_dupes:
        print(f"  Dropped {dropped_dupes:,} rows with exact duplicate ticket_text.")
    else:
        print(f"  No duplicate ticket_text rows found.")

    # ------------------------------------------------------------------
    # Step 3: Drop rows where word count < MIN_WORD_COUNT
    # ------------------------------------------------------------------
    before = len(df)
    word_counts = df["ticket_text"].str.split().str.len()
    df = df[word_counts >= MIN_WORD_COUNT]
    dropped_short = before - len(df)
    if dropped_short:
        print(f"  Dropped {dropped_short:,} rows with fewer than {MIN_WORD_COUNT} words.")
    else:
        print(f"  No rows dropped for being too short (< {MIN_WORD_COUNT} words).")

    total_dropped = initial_count - len(df)
    print(f"Total rows dropped: {total_dropped:,}  ({initial_count:,} -> {len(df):,})")

    return df.reset_index(drop=True)


def check_distribution(df: pd.DataFrame) -> list[str]:
    """Check the priority class distribution against TARGET_DISTRIBUTION.

    Args:
        df: Cleaned DataFrame with a 'priority' column.

    Returns:
        A list of warning strings, one per class that is more than TOLERANCE
        percentage points off from its target.  Empty list if all classes are
        within tolerance.
    """
    warnings: list[str] = []

    if "priority" not in df.columns or len(df) == 0:
        warnings.append("WARNING: 'priority' column missing or DataFrame is empty — cannot check distribution.")
        return warnings

    actual_counts = df["priority"].value_counts()
    total = len(df)

    for label, target_pct in TARGET_DISTRIBUTION.items():
        count = actual_counts.get(label, 0)
        actual_pct = count / total
        diff = abs(actual_pct - target_pct)
        if diff > TOLERANCE:
            warnings.append(
                f"WARNING: priority='{label}' is {actual_pct:.1%} "
                f"(target {target_pct:.1%}, diff {diff:.1%} > {TOLERANCE:.0%} tolerance)"
            )

    return warnings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Reading {TICKETS_RAW_CSV} ...")
    df_raw = pd.read_csv(TICKETS_RAW_CSV)

    # ------------------------------------------------------------------
    # Validate and clean
    # ------------------------------------------------------------------
    print("\n--- Validation & Cleaning ---")
    df_clean = validate(df_raw)

    # ------------------------------------------------------------------
    # Check priority distribution
    # ------------------------------------------------------------------
    print("\n--- Priority Distribution Check ---")
    dist_warnings = check_distribution(df_clean)
    if dist_warnings:
        for w in dist_warnings:
            print(w)
    else:
        print("  All priority classes are within the 5 pp tolerance.")

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    print(f"\n{'='*55}")
    print("  VALIDATION SUMMARY")
    print(f"{'='*55}")
    print(f"  Final row count: {len(df_clean):,}")
    if "priority" in df_clean.columns and len(df_clean) > 0:
        print(f"\n  Priority distribution:")
        total = len(df_clean)
        for label in ["low", "medium", "high", "urgent"]:
            count = (df_clean["priority"] == label).sum()
            pct = count / total
            target = TARGET_DISTRIBUTION.get(label, 0.0)
            diff_str = f"  ({pct - target:+.1%} vs target)" if abs(pct - target) > TOLERANCE else ""
            print(f"    {label:<8}  {count:>6,}  ({pct:.1%}){diff_str}")
    print(f"{'='*55}")

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_clean.to_csv(TICKETS_CSV, index=False)
    print(f"\nSaved {len(df_clean):,} rows to {TICKETS_CSV}")


if __name__ == "__main__":
    main()
