"""Tests for synthetic_data/validate.py.

Run from the repo root:
    .venv/bin/python -m pytest synthetic_data/tests/test_validate.py -v
"""

import pandas as pd
import pytest

from synthetic_data.validate import (
    MIN_WORD_COUNT,
    TARGET_DISTRIBUTION,
    check_distribution,
    validate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TICKET_COUNTER = 0


def _make_row(
    ticket_text: str = None,
    priority: str = "medium",
    ticket_id: int = None,
) -> dict:
    """Return a dict representing one fully-populated ticket row.

    Callers only need to vary the fields relevant to their test; everything
    else defaults to safe, valid values.
    """
    global _TICKET_COUNTER
    _TICKET_COUNTER += 1
    if ticket_id is None:
        ticket_id = _TICKET_COUNTER
    if ticket_text is None:
        # 15 words — safely above MIN_WORD_COUNT; unique per row to avoid dedup
        ticket_text = (
            f"My order number {_TICKET_COUNTER} has not arrived yet and I would like an update on the status please."
        )
    return {
        "ticket_id": ticket_id,
        "ticket_text": ticket_text,
        "issue_area": "Order",
        "issue_category": "Order Delivery Issues",
        "issue_sub_category": "Delayed delivery",
        "customer_sentiment": "frustrated",
        "issue_complexity": "high",
        "product_category": "Electronics",
        "product_sub_category": "Laptop",
        "priority": priority,
    }


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Wrap a list of row dicts into a DataFrame."""
    return pd.DataFrame(rows)


def _long_text(word_count: int) -> str:
    """Return a ticket_text string with exactly *word_count* words."""
    return " ".join(["word"] * word_count)


# ---------------------------------------------------------------------------
# 1. validate — drops empty ticket_text
# ---------------------------------------------------------------------------

class TestValidateDropsEmptyTicketText:
    """Rows with NaN, empty string, or whitespace-only ticket_text are removed."""

    def test_drops_nan_ticket_text(self):
        rows = [
            _make_row(ticket_text=None),   # valid default text
            _make_row(ticket_text=float("nan")),
        ]
        df = _make_df(rows)
        # Force the second row's ticket_text to actual NaN
        df.loc[1, "ticket_text"] = float("nan")
        result = validate(df)
        assert len(result) == 1, (
            f"Expected 1 row after dropping NaN ticket_text, got {len(result)}"
        )

    def test_drops_empty_string_ticket_text(self):
        rows = [_make_row(), _make_row(ticket_text="")]
        result = validate(_make_df(rows))
        assert len(result) == 1, (
            f"Expected 1 row after dropping empty ticket_text, got {len(result)}"
        )

    def test_drops_whitespace_only_ticket_text(self):
        rows = [_make_row(), _make_row(ticket_text="   "), _make_row(ticket_text="\t\n")]
        result = validate(_make_df(rows))
        assert len(result) == 1, (
            f"Expected 1 row after dropping whitespace-only ticket_text, got {len(result)}"
        )

    def test_all_empty_returns_empty_dataframe(self):
        rows = [_make_row(ticket_text=""), _make_row(ticket_text="  ")]
        result = validate(_make_df(rows))
        assert len(result) == 0, (
            "Expected empty DataFrame when all rows have empty ticket_text"
        )


# ---------------------------------------------------------------------------
# 2. validate — drops exact duplicates
# ---------------------------------------------------------------------------

class TestValidateDropsExactDuplicates:
    """Rows with identical ticket_text keep only the first occurrence."""

    def test_drops_second_of_two_identical_rows(self):
        text = _long_text(MIN_WORD_COUNT + 5)
        rows = [_make_row(ticket_text=text), _make_row(ticket_text=text)]
        result = validate(_make_df(rows))
        assert len(result) == 1, (
            f"Expected 1 row after deduplication, got {len(result)}"
        )

    def test_keeps_first_occurrence(self):
        text = _long_text(MIN_WORD_COUNT + 5)
        first_id = 9001
        rows = [
            _make_row(ticket_text=text, ticket_id=first_id),
            _make_row(ticket_text=text, ticket_id=9002),
        ]
        result = validate(_make_df(rows))
        assert result.iloc[0]["ticket_id"] == first_id, (
            "validate should keep the first occurrence of a duplicate, not the second"
        )

    def test_three_copies_yields_one_row(self):
        text = _long_text(MIN_WORD_COUNT + 5)
        rows = [_make_row(ticket_text=text) for _ in range(3)]
        result = validate(_make_df(rows))
        assert len(result) == 1, (
            f"Expected 1 row after deduplicating 3 identical rows, got {len(result)}"
        )

    def test_unique_texts_are_all_kept(self):
        rows = [_make_row(ticket_text=_long_text(MIN_WORD_COUNT + i)) for i in range(1, 4)]
        result = validate(_make_df(rows))
        assert len(result) == 3, (
            f"Expected 3 unique rows to be kept, got {len(result)}"
        )


# ---------------------------------------------------------------------------
# 3. validate — drops short ticket_text
# ---------------------------------------------------------------------------

class TestValidateDropsShortTicketText:
    """Rows with fewer than MIN_WORD_COUNT words are removed."""

    def test_drops_row_below_min_word_count(self):
        short_text = _long_text(MIN_WORD_COUNT - 1)
        rows = [_make_row(), _make_row(ticket_text=short_text)]
        result = validate(_make_df(rows))
        assert len(result) == 1, (
            f"Expected 1 row after dropping text with {MIN_WORD_COUNT - 1} words, "
            f"got {len(result)}"
        )

    def test_drops_single_word_text(self):
        rows = [_make_row(), _make_row(ticket_text="hello")]
        result = validate(_make_df(rows))
        assert len(result) == 1, (
            "Expected 1 row after dropping single-word ticket_text"
        )

    def test_keeps_row_at_exact_min_word_count(self):
        exact_text = _long_text(MIN_WORD_COUNT)
        rows = [_make_row(ticket_text=exact_text)]
        result = validate(_make_df(rows))
        assert len(result) == 1, (
            f"Row with exactly {MIN_WORD_COUNT} words should be kept, got {len(result)}"
        )

    def test_keeps_row_above_min_word_count(self):
        long_text = _long_text(MIN_WORD_COUNT + 10)
        rows = [_make_row(ticket_text=long_text)]
        result = validate(_make_df(rows))
        assert len(result) == 1, (
            f"Row with {MIN_WORD_COUNT + 10} words should be kept, got {len(result)}"
        )

    def test_all_short_returns_empty_dataframe(self):
        rows = [_make_row(ticket_text=_long_text(MIN_WORD_COUNT - 1)) for _ in range(3)]
        result = validate(_make_df(rows))
        assert len(result) == 0, (
            "Expected empty DataFrame when all rows are below MIN_WORD_COUNT"
        )


# ---------------------------------------------------------------------------
# 4. validate — preserves valid rows
# ---------------------------------------------------------------------------

class TestValidatePreservesValidRows:
    """A DataFrame of all-valid rows passes through unchanged."""

    def test_valid_dataframe_unchanged_length(self):
        rows = [_make_row() for _ in range(10)]
        df = _make_df(rows)
        result = validate(df)
        assert len(result) == len(df), (
            f"Expected {len(df)} valid rows to be preserved, got {len(result)}"
        )

    def test_valid_dataframe_preserves_all_columns(self):
        rows = [_make_row() for _ in range(3)]
        df = _make_df(rows)
        result = validate(df)
        assert set(result.columns) == set(df.columns), (
            f"validate changed the column set: "
            f"before={set(df.columns)}, after={set(result.columns)}"
        )

    def test_valid_single_row_preserved(self):
        rows = [_make_row()]
        result = validate(_make_df(rows))
        assert len(result) == 1, (
            f"Single valid row should be preserved, got {len(result)}"
        )

    def test_returns_dataframe(self):
        rows = [_make_row()]
        result = validate(_make_df(rows))
        assert isinstance(result, pd.DataFrame), (
            f"validate should return a pd.DataFrame, got {type(result).__name__}"
        )


# ---------------------------------------------------------------------------
# 5. validate — handles mixed issues
# ---------------------------------------------------------------------------

class TestValidateMixedIssues:
    """DataFrame with valid, empty, duplicate, and short rows yields correct count."""

    def test_mixed_dataframe_correct_count(self):
        shared_text = _long_text(MIN_WORD_COUNT + 5)
        rows = [
            # 3 valid, distinct rows
            _make_row(ticket_text=_long_text(MIN_WORD_COUNT + 1)),
            _make_row(ticket_text=_long_text(MIN_WORD_COUNT + 2)),
            _make_row(ticket_text=_long_text(MIN_WORD_COUNT + 3)),
            # 1 empty → dropped
            _make_row(ticket_text=""),
            # 1 short → dropped
            _make_row(ticket_text=_long_text(MIN_WORD_COUNT - 1)),
            # 2 duplicates of each other → only 1 kept
            _make_row(ticket_text=shared_text),
            _make_row(ticket_text=shared_text),
        ]
        result = validate(_make_df(rows))
        # 3 valid distinct + 1 kept from duplicates = 4
        assert len(result) == 4, (
            f"Expected 4 rows after mixed validation, got {len(result)}"
        )

    def test_empty_input_returns_empty_dataframe(self):
        df = _make_df([_make_row()])
        df = df.iloc[0:0]  # empty but schema-preserving
        result = validate(df)
        assert len(result) == 0, (
            "validate on an empty DataFrame should return an empty DataFrame"
        )
        assert isinstance(result, pd.DataFrame)

    def test_nan_and_short_both_dropped(self):
        rows = [
            _make_row(),                                    # valid
            _make_row(ticket_text=_long_text(MIN_WORD_COUNT - 1)),  # short
        ]
        df = _make_df(rows)
        df.loc[1, "ticket_text"] = float("nan")            # NaN overrides the short text
        result = validate(df)
        assert len(result) == 1, (
            f"Expected 1 row, got {len(result)}"
        )


# ---------------------------------------------------------------------------
# 6. check_distribution — on-target distribution returns empty list
# ---------------------------------------------------------------------------

class TestCheckDistributionOnTarget:
    """Returns an empty list when every class is within 5pp of TARGET_DISTRIBUTION."""

    def _make_on_target_df(self, n: int = 200) -> pd.DataFrame:
        """Build a DataFrame that exactly matches TARGET_DISTRIBUTION proportions."""
        rows = []
        for priority, fraction in TARGET_DISTRIBUTION.items():
            count = int(round(fraction * n))
            rows.extend([_make_row(priority=priority) for _ in range(count)])
        return _make_df(rows)

    def test_exact_distribution_returns_empty_list(self):
        df = self._make_on_target_df(n=200)
        warnings = check_distribution(df)
        assert warnings == [], (
            f"Expected no warnings for on-target distribution, got: {warnings}"
        )

    def test_returns_list_type(self):
        df = self._make_on_target_df(n=200)
        result = check_distribution(df)
        assert isinstance(result, list), (
            f"check_distribution should return a list, got {type(result).__name__}"
        )

    def test_slightly_off_within_tolerance_no_warnings(self):
        """Distribution 1pp off per class is still within 5pp — no warnings."""
        # 100 rows: low=21, medium=34, high=30, urgent=15 → low is 1pp off
        rows = (
            [_make_row(priority="low")] * 21
            + [_make_row(priority="medium")] * 34
            + [_make_row(priority="high")] * 30
            + [_make_row(priority="urgent")] * 15
        )
        result = check_distribution(_make_df(rows))
        assert result == [], (
            f"Expected no warnings for distribution within 5pp tolerance, got: {result}"
        )


# ---------------------------------------------------------------------------
# 7. check_distribution — returns warnings when a class is off by >5pp
# ---------------------------------------------------------------------------

class TestCheckDistributionOffTarget:
    """Returns warning strings when any class deviates more than 5pp from target."""

    def test_warning_returned_for_off_target_class(self):
        # low target=20%, actual=30% → 10pp off
        rows = (
            [_make_row(priority="low")] * 30
            + [_make_row(priority="medium")] * 35
            + [_make_row(priority="high")] * 25
            + [_make_row(priority="urgent")] * 10
        )
        warnings = check_distribution(_make_df(rows))
        assert len(warnings) > 0, (
            "Expected at least one warning when 'low' is 10pp off target"
        )

    def test_warning_mentions_offending_class(self):
        # low target=20%, actual=30% → 10pp off
        rows = (
            [_make_row(priority="low")] * 30
            + [_make_row(priority="medium")] * 35
            + [_make_row(priority="high")] * 25
            + [_make_row(priority="urgent")] * 10
        )
        warnings = check_distribution(_make_df(rows))
        combined = " ".join(warnings).lower()
        assert "low" in combined, (
            f"Warning should mention the offending class 'low'; got: {warnings}"
        )

    def test_warnings_are_strings(self):
        rows = (
            [_make_row(priority="low")] * 30
            + [_make_row(priority="medium")] * 35
            + [_make_row(priority="high")] * 25
            + [_make_row(priority="urgent")] * 10
        )
        warnings = check_distribution(_make_df(rows))
        for w in warnings:
            assert isinstance(w, str), (
                f"Each warning should be a str, got {type(w).__name__}: {w!r}"
            )

    def test_multiple_off_target_classes_each_get_warning(self):
        # low=35% (+15pp), urgent=5% (-10pp); medium and high absorb remainder
        rows = (
            [_make_row(priority="low")] * 35
            + [_make_row(priority="medium")] * 35
            + [_make_row(priority="high")] * 25
            + [_make_row(priority="urgent")] * 5
        )
        warnings = check_distribution(_make_df(rows))
        combined = " ".join(warnings).lower()
        assert "low" in combined, "Expected a warning mentioning 'low'"
        assert "urgent" in combined, "Expected a warning mentioning 'urgent'"


# ---------------------------------------------------------------------------
# 8. check_distribution — handles missing priority class
# ---------------------------------------------------------------------------

class TestCheckDistributionMissingClass:
    """Missing classes (0% actual vs non-zero target) produce a warning."""

    def test_missing_urgent_produces_warning(self):
        # No "urgent" rows at all → 0% vs 15% target = 15pp off
        rows = (
            [_make_row(priority="low")] * 20
            + [_make_row(priority="medium")] * 45
            + [_make_row(priority="high")] * 35
        )
        warnings = check_distribution(_make_df(rows))
        assert len(warnings) > 0, (
            "Expected a warning when 'urgent' class is entirely missing"
        )

    def test_missing_class_warning_mentions_class(self):
        rows = (
            [_make_row(priority="low")] * 20
            + [_make_row(priority="medium")] * 45
            + [_make_row(priority="high")] * 35
        )
        warnings = check_distribution(_make_df(rows))
        combined = " ".join(warnings).lower()
        assert "urgent" in combined, (
            f"Warning should mention 'urgent' when it is missing; got: {warnings}"
        )

    def test_only_one_class_present_produces_warnings(self):
        # All rows are "high" → low, medium, urgent are 0%
        rows = [_make_row(priority="high")] * 100
        warnings = check_distribution(_make_df(rows))
        assert len(warnings) >= 3, (
            f"Expected warnings for all missing classes, got {len(warnings)}: {warnings}"
        )


# ---------------------------------------------------------------------------
# 9. TARGET_DISTRIBUTION sums to ~1.0
# ---------------------------------------------------------------------------

class TestTargetDistribution:
    """TARGET_DISTRIBUTION is a dict whose values sum to approximately 1.0."""

    def test_target_distribution_is_dict(self):
        assert isinstance(TARGET_DISTRIBUTION, dict), (
            f"TARGET_DISTRIBUTION should be dict, got {type(TARGET_DISTRIBUTION).__name__}"
        )

    def test_target_distribution_sums_to_one(self):
        total = sum(TARGET_DISTRIBUTION.values())
        assert abs(total - 1.0) < 1e-9, (
            f"TARGET_DISTRIBUTION values should sum to 1.0, got {total}"
        )

    def test_target_distribution_contains_all_priority_classes(self):
        expected_classes = {"low", "medium", "high", "urgent"}
        assert set(TARGET_DISTRIBUTION.keys()) == expected_classes, (
            f"Expected classes {expected_classes}, got {set(TARGET_DISTRIBUTION.keys())}"
        )

    def test_target_distribution_low(self):
        assert TARGET_DISTRIBUTION["low"] == pytest.approx(0.20), (
            f"Expected 'low' = 0.20, got {TARGET_DISTRIBUTION['low']}"
        )

    def test_target_distribution_medium(self):
        assert TARGET_DISTRIBUTION["medium"] == pytest.approx(0.35), (
            f"Expected 'medium' = 0.35, got {TARGET_DISTRIBUTION['medium']}"
        )

    def test_target_distribution_high(self):
        assert TARGET_DISTRIBUTION["high"] == pytest.approx(0.30), (
            f"Expected 'high' = 0.30, got {TARGET_DISTRIBUTION['high']}"
        )

    def test_target_distribution_urgent(self):
        assert TARGET_DISTRIBUTION["urgent"] == pytest.approx(0.15), (
            f"Expected 'urgent' = 0.15, got {TARGET_DISTRIBUTION['urgent']}"
        )

    def test_all_values_are_floats(self):
        for cls, val in TARGET_DISTRIBUTION.items():
            assert isinstance(val, float), (
                f"TARGET_DISTRIBUTION['{cls}'] should be float, got {type(val).__name__}"
            )


# ---------------------------------------------------------------------------
# 10. MIN_WORD_COUNT is 10
# ---------------------------------------------------------------------------

class TestMinWordCount:
    """MIN_WORD_COUNT must be exactly 10."""

    def test_min_word_count_is_10(self):
        assert MIN_WORD_COUNT == 10, (
            f"Expected MIN_WORD_COUNT == 10, got {MIN_WORD_COUNT}"
        )

    def test_min_word_count_is_int(self):
        assert isinstance(MIN_WORD_COUNT, int), (
            f"MIN_WORD_COUNT should be int, got {type(MIN_WORD_COUNT).__name__}"
        )
