"""Tests for synthetic_data/generate.py.

Run from the repo root:
    .venv/bin/python -m pytest synthetic_data/tests/test_generate.py -v
"""

import pytest

from synthetic_data.generate import (
    BATCH_SIZE,
    SYSTEM_PROMPT,
    build_batch_prompt,
    extract_json_array,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SAMPLE_ROW = {
    "issue_area": "Order",
    "issue_category": "Order Delivery Issues",
    "issue_sub_category": "Delayed delivery",
    "customer_sentiment": "frustrated",
    "issue_complexity": "high",
    "product_category": "Electronics",
    "product_sub_category": "Laptop",
}

FIVE_ROWS = [SAMPLE_ROW] * 5

ONE_ROW = [SAMPLE_ROW]


# ---------------------------------------------------------------------------
# 1. BATCH_SIZE
# ---------------------------------------------------------------------------

class TestBatchSize:
    """BATCH_SIZE must be exactly 5."""

    def test_batch_size_is_5(self):
        assert BATCH_SIZE == 5, f"Expected BATCH_SIZE == 5, got {BATCH_SIZE}"

    def test_batch_size_is_int(self):
        assert isinstance(BATCH_SIZE, int), (
            f"BATCH_SIZE should be int, got {type(BATCH_SIZE).__name__}"
        )


# ---------------------------------------------------------------------------
# 2. SYSTEM_PROMPT
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    """SYSTEM_PROMPT must be a non-empty string."""

    def test_system_prompt_is_str(self):
        assert isinstance(SYSTEM_PROMPT, str), (
            f"SYSTEM_PROMPT should be str, got {type(SYSTEM_PROMPT).__name__}"
        )

    def test_system_prompt_is_non_empty(self):
        assert SYSTEM_PROMPT.strip(), "SYSTEM_PROMPT must not be empty or whitespace-only"


# ---------------------------------------------------------------------------
# 3. build_batch_prompt
# ---------------------------------------------------------------------------

class TestBuildBatchPrompt:
    """build_batch_prompt(rows) returns a well-formed prompt string."""

    # --- Return type ---

    def test_returns_string(self):
        result = build_batch_prompt(FIVE_ROWS)
        assert isinstance(result, str), (
            f"build_batch_prompt should return str, got {type(result).__name__}"
        )

    # --- Contains batch count reference ---

    def test_contains_batch_count_full_batch(self):
        result = build_batch_prompt(FIVE_ROWS)
        assert "5" in result, (
            "Prompt for 5 rows should reference the count '5'"
        )

    def test_contains_json_array_instruction(self):
        result = build_batch_prompt(FIVE_ROWS)
        assert "JSON array" in result or "json array" in result.lower(), (
            "Prompt should instruct the model to return a JSON array"
        )

    # --- Metadata values appear in the prompt ---

    def test_contains_issue_area(self):
        result = build_batch_prompt(ONE_ROW)
        assert SAMPLE_ROW["issue_area"] in result, (
            "Prompt should contain the issue_area value"
        )

    def test_contains_issue_category(self):
        result = build_batch_prompt(ONE_ROW)
        assert SAMPLE_ROW["issue_category"] in result, (
            "Prompt should contain the issue_category value"
        )

    def test_contains_issue_sub_category(self):
        result = build_batch_prompt(ONE_ROW)
        assert SAMPLE_ROW["issue_sub_category"] in result, (
            "Prompt should contain the issue_sub_category value"
        )

    def test_contains_customer_sentiment(self):
        result = build_batch_prompt(ONE_ROW)
        assert SAMPLE_ROW["customer_sentiment"] in result, (
            "Prompt should contain the customer_sentiment value"
        )

    def test_contains_issue_complexity(self):
        result = build_batch_prompt(ONE_ROW)
        assert SAMPLE_ROW["issue_complexity"] in result, (
            "Prompt should contain the issue_complexity value"
        )

    def test_contains_product_category(self):
        result = build_batch_prompt(ONE_ROW)
        assert SAMPLE_ROW["product_category"] in result, (
            "Prompt should contain the product_category value"
        )

    def test_contains_product_sub_category(self):
        result = build_batch_prompt(ONE_ROW)
        assert SAMPLE_ROW["product_sub_category"] in result, (
            "Prompt should contain the product_sub_category value"
        )

    # --- Scenarios are numbered ---

    def test_one_row_contains_numbering(self):
        result = build_batch_prompt(ONE_ROW)
        assert "1." in result, (
            "Prompt for 1 row should number the scenario as '1.'"
        )

    def test_five_rows_contains_all_numbering(self):
        result = build_batch_prompt(FIVE_ROWS)
        for i in range(1, 6):
            assert f"{i}." in result, (
                f"Prompt for 5 rows should number scenario '{i}.'"
            )

    # --- Works with a partial (single-row) final batch ---

    def test_one_row_batch_works(self):
        result = build_batch_prompt(ONE_ROW)
        assert result, "build_batch_prompt should return a non-empty string for 1 row"

    # --- Works with a full five-row batch ---

    def test_five_row_batch_works(self):
        result = build_batch_prompt(FIVE_ROWS)
        assert result, "build_batch_prompt should return a non-empty string for 5 rows"

    # --- Distinct rows produce distinct metadata in the prompt ---

    def test_different_rows_appear_distinctly(self):
        row_a = {**SAMPLE_ROW, "issue_sub_category": "Package not delivered"}
        row_b = {**SAMPLE_ROW, "issue_sub_category": "Delayed delivery"}
        result = build_batch_prompt([row_a, row_b])
        assert "Package not delivered" in result
        assert "Delayed delivery" in result


# ---------------------------------------------------------------------------
# 4. extract_json_array
# ---------------------------------------------------------------------------

class TestExtractJsonArray:
    """extract_json_array(raw) parses a JSON array of strings from LLM output."""

    # --- Happy-path: clean array ---

    def test_clean_json_array(self):
        raw = '["text1", "text2", "text3"]'
        result = extract_json_array(raw)
        assert result == ["text1", "text2", "text3"], (
            f"Expected ['text1', 'text2', 'text3'], got {result!r}"
        )

    # --- Happy-path: markdown fences ---

    def test_markdown_fenced_json_array(self):
        raw = '```json\n["a","b"]\n```'
        result = extract_json_array(raw)
        assert result == ["a", "b"], (
            f"Expected ['a', 'b'], got {result!r}"
        )

    # --- Happy-path: single-element array ---

    def test_single_element_array(self):
        raw = '["only one"]'
        result = extract_json_array(raw)
        assert result == ["only one"], (
            f"Expected ['only one'], got {result!r}"
        )

    # --- Happy-path: array embedded in surrounding text ---

    def test_array_embedded_in_surrounding_text(self):
        raw = 'Here are your results:\n["ticket one", "ticket two"]\nEnd of output.'
        result = extract_json_array(raw)
        assert result == ["ticket one", "ticket two"], (
            f"Expected ['ticket one', 'ticket two'], got {result!r}"
        )

    # --- Failure cases: returns None ---

    def test_empty_string_returns_none(self):
        result = extract_json_array("")
        assert result is None, f"Expected None for empty string, got {result!r}"

    def test_plain_text_returns_none(self):
        result = extract_json_array("This is just plain text with no JSON at all.")
        assert result is None, f"Expected None for plain text, got {result!r}"

    def test_json_object_returns_none(self):
        result = extract_json_array('{"key": "val"}')
        assert result is None, (
            f"Expected None for a JSON object (not array), got {result!r}"
        )

    def test_array_of_non_strings_returns_none(self):
        result = extract_json_array("[1, 2, 3]")
        assert result is None, (
            f"Expected None for an array of non-strings, got {result!r}"
        )

    # --- Return type when successful ---

    def test_returns_list_on_success(self):
        result = extract_json_array('["x", "y"]')
        assert isinstance(result, list), (
            f"Successful parse should return list, got {type(result).__name__}"
        )

    def test_all_elements_are_strings(self):
        result = extract_json_array('["hello", "world"]')
        assert result is not None
        for elem in result:
            assert isinstance(elem, str), (
                f"All elements should be str, found {type(elem).__name__}: {elem!r}"
            )
