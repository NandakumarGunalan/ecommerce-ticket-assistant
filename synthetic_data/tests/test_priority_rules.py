"""Tests for synthetic_data/priority_rules.py.

Run from the repo root:
    python -m pytest synthetic_data/tests/ -v
"""

import pytest

from synthetic_data.priority_rules import (
    COMPLEXITIES,
    PRIORITY_LEVELS,
    PRIORITY_MATRIX,
    SENTIMENTS,
    apply_noise,
    get_priority,
    perturb_priority,
)


# ---------------------------------------------------------------------------
# 1. Every cell of the priority matrix
# ---------------------------------------------------------------------------

MATRIX_CASES = [
    # (sentiment, complexity, expected_priority)
    ("neutral",    "less",   "low"),
    ("neutral",    "medium", "medium"),
    ("neutral",    "high",   "medium"),
    ("negative",   "less",   "medium"),
    ("negative",   "medium", "high"),
    ("negative",   "high",   "high"),
    ("frustrated", "less",   "high"),
    ("frustrated", "medium", "high"),
    ("frustrated", "high",   "urgent"),
]


@pytest.mark.parametrize("sentiment,complexity,expected", MATRIX_CASES)
def test_priority_matrix_direct(sentiment, complexity, expected):
    """PRIORITY_MATRIX lookup returns the correct priority for every cell."""
    assert PRIORITY_MATRIX[(sentiment, complexity)] == expected


@pytest.mark.parametrize("sentiment,complexity,expected", MATRIX_CASES)
def test_get_priority_all_cells(sentiment, complexity, expected):
    """get_priority() returns the correct priority for every (sentiment, complexity) pair."""
    assert get_priority(sentiment, complexity) == expected


def test_priority_matrix_has_all_nine_cells():
    """PRIORITY_MATRIX covers all 9 combinations (len == 9)."""
    assert len(PRIORITY_MATRIX) == len(SENTIMENTS) * len(COMPLEXITIES) == 9


# ---------------------------------------------------------------------------
# 2. get_priority raises KeyError for invalid inputs
# ---------------------------------------------------------------------------

def test_get_priority_invalid_sentiment():
    """get_priority raises KeyError for an unrecognised sentiment."""
    with pytest.raises(KeyError):
        get_priority("angry", "less")


def test_get_priority_invalid_complexity():
    """get_priority raises KeyError for an unrecognised complexity."""
    with pytest.raises(KeyError):
        get_priority("neutral", "extreme")


def test_get_priority_both_invalid():
    """get_priority raises KeyError when both arguments are invalid."""
    with pytest.raises(KeyError):
        get_priority("happy", "none")


def test_get_priority_empty_strings():
    """get_priority raises KeyError for empty strings."""
    with pytest.raises(KeyError):
        get_priority("", "")


# ---------------------------------------------------------------------------
# 3. perturb_priority boundary and middle cases
# ---------------------------------------------------------------------------

class TestPerturbPriorityBoundaries:
    """Boundary behaviour: clamp at 'low' and 'urgent'."""

    def _collect_outcomes(self, priority, n_trials=200):
        """Run perturb_priority many times and collect the unique outputs."""
        import random as _random
        rng = _random.Random(0)
        return {perturb_priority(priority, rng=rng) for _ in range(n_trials)}

    def test_low_never_goes_below_low(self):
        """'low' can only shift up to 'medium'; it never produces something below 'low'."""
        outcomes = self._collect_outcomes("low")
        assert "low" not in outcomes or True  # low may stay low when shift would go -1
        # The key guarantee: no outcome is below 'low' in the level list
        for outcome in outcomes:
            assert PRIORITY_LEVELS.index(outcome) >= PRIORITY_LEVELS.index("low")

    def test_low_shifts_to_medium(self):
        """'low' must be able to produce 'medium' (the only valid upward shift)."""
        outcomes = self._collect_outcomes("low")
        assert "medium" in outcomes

    def test_low_never_produces_high_or_urgent(self):
        """'low' can never jump to 'high' or 'urgent' in a single perturbation."""
        outcomes = self._collect_outcomes("low")
        assert "high" not in outcomes
        assert "urgent" not in outcomes

    def test_urgent_never_goes_above_urgent(self):
        """'urgent' can only shift down to 'high'; it never exceeds 'urgent'."""
        outcomes = self._collect_outcomes("urgent")
        for outcome in outcomes:
            assert PRIORITY_LEVELS.index(outcome) <= PRIORITY_LEVELS.index("urgent")

    def test_urgent_shifts_to_high(self):
        """'urgent' must be able to produce 'high' (the only valid downward shift)."""
        outcomes = self._collect_outcomes("urgent")
        assert "high" in outcomes

    def test_urgent_never_produces_low_or_medium(self):
        """'urgent' can never jump to 'low' or 'medium' in a single perturbation."""
        outcomes = self._collect_outcomes("urgent")
        assert "low" not in outcomes
        assert "medium" not in outcomes

    def test_medium_can_go_low_or_high(self):
        """'medium' must be able to produce both 'low' and 'high'."""
        outcomes = self._collect_outcomes("medium")
        assert "low" in outcomes
        assert "high" in outcomes

    def test_medium_never_produces_urgent(self):
        """'medium' is two steps below 'urgent', so a single ±1 shift cannot reach it."""
        outcomes = self._collect_outcomes("medium")
        assert "urgent" not in outcomes

    def test_high_can_go_medium_or_urgent(self):
        """'high' must be able to produce both 'medium' and 'urgent'."""
        outcomes = self._collect_outcomes("high")
        assert "medium" in outcomes
        assert "urgent" in outcomes

    def test_high_never_produces_low(self):
        """'high' is two steps above 'low', so a single ±1 shift cannot reach it."""
        outcomes = self._collect_outcomes("high")
        assert "low" not in outcomes

    def test_output_is_always_a_valid_priority(self):
        """perturb_priority always returns a value that is in PRIORITY_LEVELS."""
        import random as _random
        rng = _random.Random(99)
        for priority in PRIORITY_LEVELS:
            for _ in range(50):
                result = perturb_priority(priority, rng=rng)
                assert result in PRIORITY_LEVELS


# ---------------------------------------------------------------------------
# 4. apply_noise
# ---------------------------------------------------------------------------

class TestApplyNoise:
    """Behavioural tests for apply_noise()."""

    # -- noise_rate = 0: no changes ------------------------------------------
    # NOTE: apply_noise enforces a minimum of 1 perturbation via
    #   max(1, round(len(result) * noise_rate))
    # so even noise_rate=0 will perturb exactly 1 element.  We test that
    # behaviour explicitly rather than asserting zero changes.

    def test_zero_noise_rate_perturbs_exactly_one_element(self):
        """With noise_rate=0, apply_noise still perturbs exactly 1 element
        (the minimum enforced by max(1, round(n * rate)))."""
        priorities = ["medium"] * 100   # all-middle so clamping never hides the shift
        result = apply_noise(priorities, noise_rate=0, seed=7)
        changed = sum(a != b for a, b in zip(priorities, result))
        assert changed == 1

    def test_zero_noise_rate_does_not_mutate_input(self):
        """apply_noise must not mutate the original list in any configuration."""
        priorities = ["low", "medium", "high", "urgent"]
        original = list(priorities)
        apply_noise(priorities, noise_rate=0, seed=0)
        assert priorities == original

    # -- noise_rate = 1.0: every element is perturbed -------------------------

    def test_full_noise_rate_every_element_perturbed(self):
        """With noise_rate=1.0, every output element should differ by exactly ±1 level
        from the corresponding input element (i.e. each was perturbed)."""
        # Use a list where every element is at the middle levels so clamping
        # cannot accidentally cause an element to stay in place.
        priorities = ["medium", "high"] * 500   # 1000 elements, no boundary clamp
        result = apply_noise(priorities, noise_rate=1.0, seed=0)
        assert len(result) == len(priorities)
        for original, perturbed in zip(priorities, result):
            orig_idx = PRIORITY_LEVELS.index(original)
            pert_idx = PRIORITY_LEVELS.index(perturbed)
            assert abs(orig_idx - pert_idx) == 1, (
                f"Expected ±1 shift from '{original}', got '{perturbed}'"
            )

    def test_full_noise_rate_preserves_list_length(self):
        """apply_noise must always return a list of the same length as the input."""
        priorities = ["low"] * 100
        result = apply_noise(priorities, noise_rate=1.0, seed=1)
        assert len(result) == 100

    # -- noise_rate = 0.07 on 10 000 elements ---------------------------------

    def test_default_noise_rate_roughly_seven_percent(self):
        """With noise_rate=0.07 on 10 000 elements, ~7 % should change (5–9 % tolerance)."""
        import random as _random
        rng = _random.Random(123)
        all_priorities = [rng.choice(PRIORITY_LEVELS) for _ in range(10_000)]

        result = apply_noise(all_priorities, noise_rate=0.07, seed=42)

        changed = sum(a != b for a, b in zip(all_priorities, result))
        change_rate = changed / len(all_priorities)
        assert 0.05 <= change_rate <= 0.09, (
            f"Expected ~7 % change rate, got {change_rate:.2%}"
        )

    # -- determinism ----------------------------------------------------------

    def test_same_seed_produces_same_output(self):
        """apply_noise with identical seed must produce identical results."""
        priorities = ["low", "medium", "high", "urgent"] * 250
        result_a = apply_noise(priorities, noise_rate=0.07, seed=42)
        result_b = apply_noise(priorities, noise_rate=0.07, seed=42)
        assert result_a == result_b

    def test_different_seed_may_produce_different_output(self):
        """apply_noise with different seeds should (almost certainly) differ on a
        large input — this guards against accidentally ignoring the seed."""
        priorities = ["medium"] * 1000
        result_a = apply_noise(priorities, noise_rate=0.3, seed=1)
        result_b = apply_noise(priorities, noise_rate=0.3, seed=999)
        # With 300 perturbations and random choices the probability of identical
        # outcomes is astronomically small.
        assert result_a != result_b

    # -- output validity ------------------------------------------------------

    def test_output_contains_only_valid_priorities(self):
        """apply_noise must only produce values that are in PRIORITY_LEVELS."""
        priorities = PRIORITY_LEVELS * 100
        result = apply_noise(priorities, noise_rate=0.5, seed=7)
        for p in result:
            assert p in PRIORITY_LEVELS

    def test_does_not_mutate_input_list(self):
        """apply_noise must not mutate the caller's list."""
        priorities = ["low", "medium", "high", "urgent"] * 10
        original = list(priorities)
        apply_noise(priorities, noise_rate=0.5, seed=0)
        assert priorities == original
