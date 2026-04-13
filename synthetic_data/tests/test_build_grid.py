"""Tests for synthetic_data/build_grid.py.

Run from the repo root:
    .venv/bin/python -m pytest synthetic_data/tests/test_build_grid.py -v
"""

import itertools
from collections import Counter

import pytest

from synthetic_data.build_grid import ISSUE_TRIPLES, PRODUCT_COMBOS
from synthetic_data.priority_rules import (
    COMPLEXITIES,
    SENTIMENTS,
    apply_noise,
    get_priority,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_grid_rows():
    """Reconstruct the full grid in-memory without writing any files.

    Mirrors the logic expected in build_grid.main(), but returns a list of
    dicts so tests can inspect every column without touching the filesystem.
    """
    rows = []
    ticket_id = 1
    for issue_triple, product_combo, sentiment, complexity in itertools.product(
        ISSUE_TRIPLES, PRODUCT_COMBOS, SENTIMENTS, COMPLEXITIES
    ):
        issue_area, issue_category, issue_sub_category = issue_triple
        product_category, product_sub_category = product_combo
        rows.append(
            {
                "ticket_id": ticket_id,
                "issue_area": issue_area,
                "issue_category": issue_category,
                "issue_sub_category": issue_sub_category,
                "customer_sentiment": sentiment,
                "issue_complexity": complexity,
                "product_category": product_category,
                "product_sub_category": product_sub_category,
                "priority": get_priority(sentiment, complexity),
            }
        )
        ticket_id += 1
    return rows


# Build once; all grid tests share this list.
_GRID = _build_grid_rows()

EXPECTED_COLUMNS = [
    "ticket_id",
    "issue_area",
    "issue_category",
    "issue_sub_category",
    "customer_sentiment",
    "issue_complexity",
    "product_category",
    "product_sub_category",
    "priority",
]

EXPECTED_ISSUE_AREAS = {
    "Order": 14,
    "Cancellations and returns": 14,
    "Login and Account": 11,
    "Shopping": 11,
    "Shipping": 8,
    "Warranty": 12,
}

EXPECTED_PRODUCT_CATEGORIES = {
    "Electronics": 6,
    "Appliances": 6,
    "Men/Women/Kids": 6,
}

VALID_PRIORITIES = {"low", "medium", "high", "urgent"}

# All issue areas, categories, sub-categories taken verbatim from PLAN.md.
EXPECTED_ISSUE_TRIPLES = [
    # --- Order (14) ---
    ("Order", "Order Delivery Issues", "Delayed delivery"),
    ("Order", "Order Delivery Issues", "Package not delivered"),
    ("Order", "Order Delivery Issues", "Package shows as delivered but cannot be found"),
    ("Order", "Order Delivery Issues", "Delivery to Wrong Address"),
    ("Order", "Order Delivery Issues", "Missed delivery"),
    ("Order", "Order Delivery Issues", "Order approved but not shipped"),
    ("Order", "Order Confirmation and Status", "Tracking/Shipping Updates"),
    ("Order", "Order Confirmation and Status", "Confirming order status"),
    ("Order", "Invoice and Payment", "Missing invoice"),
    ("Order", "Invoice and Payment", "Billing Discrepancies/Overcharging"),
    ("Order", "Returns and Refunds", "Damaged Goods/Poor Packaging"),
    ("Order", "Returns and Refunds", "Seller's returns policy"),
    ("Order", "Product Installation", "Installation after delivery"),
    ("Order", "Expedited Delivery", "Faster delivery options"),
    # --- Cancellations and returns (14) ---
    ("Cancellations and returns", "Return and Exchange", "Returning or exchanging an item"),
    ("Cancellations and returns", "Return and Exchange", "Refund Delays/Complications"),
    ("Cancellations and returns", "Return and Exchange", "Refund timelines for cancellation or returns"),
    ("Cancellations and returns", "Return and Exchange", "Eligibility Disputes"),
    ("Cancellations and returns", "Return and Exchange", "Return Shipping/Pickup Issues"),
    ("Cancellations and returns", "Return and Exchange", "Package open or tampered on delivery"),
    ("Cancellations and returns", "Replacement and Return Process", "Replacement item arrived damaged"),
    ("Cancellations and returns", "Replacement and Return Process", "Time frame for receiving a replacement"),
    ("Cancellations and returns", "Replacement and Return Process", "Products not eligible for returns"),
    ("Cancellations and returns", "Pickup and Shipping", "Pickup process"),
    ("Cancellations and returns", "Pickup and Shipping", "Refund not received in bank account"),
    ("Cancellations and returns", "Cash on Delivery (CoD) Refunds", "Refund timelines for Cash on Delivery  returns"),
    ("Cancellations and returns", "Cash on Delivery (CoD) Refunds", "Refund process for items paid for with Cash on Delivery  "),
    ("Cancellations and returns", "Order Cancellation", "Time taken to cancel an order"),
    # --- Login and Account (11) ---
    ("Login and Account", "Mobile Number and Email Verification", "Issues with receiving the OTP or verification code"),
    ("Login and Account", "Mobile Number and Email Verification", "Changing the email ID linked to the account"),
    ("Login and Account", "Mobile Number and Email Verification", "Verification requirement for mobile number or email address during login"),
    ("Login and Account", "Account Reactivation and Deactivation", "Reactivating an inactive account"),
    ("Login and Account", "Account Reactivation and Deactivation", "Deactivating the account"),
    ("Login and Account", "Adding and Changing Account Information", "Changing the password for the account"),
    ("Login and Account", "Adding and Changing Account Information", "Adding a new delivery address to the account"),
    ("Login and Account", "Adding and Changing Account Information", "Using a new email address to log in to the account"),
    ("Login and Account", "Login Issues and Error Messages", "Accessing the account when the phone is lost and the password is forgotten"),
    ("Login and Account", "Login Issues and Error Messages", "Error message regarding exceeded attempts to enter the correct verification code"),
    ("Login and Account", "Login Issues and Error Messages", "Unable to log in after app update"),
    # --- Shopping (11) ---
    ("Shopping", "Pricing and Discounts", "Different prices for the same product"),
    ("Shopping", "Pricing and Discounts", "Discount/Promotion Application"),
    ("Shopping", "Pricing and Discounts", "Discounts through exchange offers"),
    ("Shopping", "Pricing and Discounts", "Instant cashback"),
    ("Shopping", "Product Availability and Status", "Ordering 'Out of Stock' or 'Temporarily Unavailable' products"),
    ("Shopping", "Product Availability and Status", "International shipping"),
    ("Shopping", "Account and Shopping", "Account requirement for shopping"),
    ("Shopping", "Account and Shopping", "Hidden charges"),
    ("Shopping", "Loyalty program", "Loyalty points clarifications"),
    ("Shopping", "Loyalty program", "Reward points redemption"),
    ("Shopping", "Pricing and Discounts", "Price increased after adding to cart"),
    # --- Shipping (8) ---
    ("Shipping", "Availability of Faster Delivery Options", "Inability to get some items shipped to a location"),
    ("Shipping", "Availability of Faster Delivery Options", "Unavailability of faster delivery options at a specific location"),
    ("Shipping", "Standard Shipping Speeds and Delivery Charges", "Queries regarding delivery charges"),
    ("Shipping", "Standard Shipping Speeds and Delivery Charges", "Understanding the standard shipping speeds"),
    ("Shipping", "Free Delivery Qualification", "Reasons for an order not qualifying for free delivery"),
    ("Shipping", "Standard Shipping Speeds and Delivery Charges", "Delivery charges applied unexpectedly"),
    ("Shipping", "Shipping Options for Returns", "Availability of faster delivery options (e.g., Same Day & In-a-Day) for return requests"),
    ("Shipping", "Contacting Seller's Partnered Courier Service Providers", "How to get in touch with the courier service providers associated with sellers"),
    # --- Warranty (12) ---
    ("Warranty", "Warranty Claim Process", "Steps to claim warranty for a product"),
    ("Warranty", "Warranty Claim Process", "Claiming warranty on replacement products"),
    ("Warranty", "Warranty Terms and Changes", "Warranty mismatch between the website and the brand's website"),
    ("Warranty", "Warranty Terms and Changes", "Impact of changes in warranty terms on the product"),
    ("Warranty", "Accessing Warranty Details", "Retrieving warranty details for a product"),
    ("Warranty", "Accessing Warranty Details", "Finding warranty details when the details are not remembered"),
    ("Warranty", "Lost or Missing Warranty Card", "Obtaining warranty without a warranty card"),
    ("Warranty", "Lost or Missing Warranty Card", "Process of claiming warranty without a warranty card"),
    ("Warranty", "Product Registration and Warranty", "Applicability of warranty for products purchased online"),
    ("Warranty", "Product Registration and Warranty", "Need to register the product with the brand for warranty benefits"),
    ("Warranty", "Start Date of Warranty", "Determining the applicable start date for the warranty"),
    ("Warranty", "Extended Warranty", "Process of signing up for an extended warranty for a product"),
]

EXPECTED_PRODUCT_COMBOS = [
    ("Electronics", "Laptop"),
    ("Electronics", "Mobile"),
    ("Electronics", "Television"),
    ("Electronics", "Tablet"),
    ("Electronics", "Headphone"),
    ("Electronics", "DSLR Camera"),
    ("Appliances", "Refrigerator"),
    ("Appliances", "Washing Machine"),
    ("Appliances", "Microwave Oven"),
    ("Appliances", "Air Conditioner"),
    ("Appliances", "Water Purifier"),
    ("Appliances", "Oven Toaster Grills (OTG)"),
    ("Men/Women/Kids", "T-Shirt"),
    ("Men/Women/Kids", "Jeans"),
    ("Men/Women/Kids", "Shoes"),
    ("Men/Women/Kids", "Toy"),
    ("Men/Women/Kids", "Backpack"),
    ("Men/Women/Kids", "Wrist Watch"),
]


# ---------------------------------------------------------------------------
# 1. Taxonomy counts
# ---------------------------------------------------------------------------

class TestTaxonomyCounts:
    """ISSUE_TRIPLES has 70 entries; PRODUCT_COMBOS has 18 entries."""

    def test_issue_triples_total_count(self):
        assert len(ISSUE_TRIPLES) == 70, (
            f"Expected 70 issue triples, got {len(ISSUE_TRIPLES)}"
        )

    def test_product_combos_total_count(self):
        assert len(PRODUCT_COMBOS) == 18, (
            f"Expected 18 product combos, got {len(PRODUCT_COMBOS)}"
        )


# ---------------------------------------------------------------------------
# 2. Issue area distribution
# ---------------------------------------------------------------------------

class TestIssueAreaDistribution:
    """Each issue area must have exactly the specified number of triples."""

    def _area_counts(self):
        return Counter(area for area, _cat, _sub in ISSUE_TRIPLES)

    @pytest.mark.parametrize("area,expected_count", EXPECTED_ISSUE_AREAS.items())
    def test_issue_area_count(self, area, expected_count):
        counts = self._area_counts()
        assert counts[area] == expected_count, (
            f"Issue area '{area}': expected {expected_count} triples, got {counts[area]}"
        )

    def test_no_unexpected_issue_areas(self):
        counts = self._area_counts()
        unexpected = set(counts.keys()) - set(EXPECTED_ISSUE_AREAS.keys())
        assert not unexpected, f"Unexpected issue areas found: {unexpected}"


# ---------------------------------------------------------------------------
# 3. Product category distribution
# ---------------------------------------------------------------------------

class TestProductCategoryDistribution:
    """Each product category must have exactly 6 sub-categories."""

    def _category_counts(self):
        return Counter(cat for cat, _sub in PRODUCT_COMBOS)

    @pytest.mark.parametrize("category,expected_count", EXPECTED_PRODUCT_CATEGORIES.items())
    def test_product_category_count(self, category, expected_count):
        counts = self._category_counts()
        assert counts[category] == expected_count, (
            f"Product category '{category}': expected {expected_count} combos, got {counts[category]}"
        )

    def test_no_unexpected_product_categories(self):
        counts = self._category_counts()
        unexpected = set(counts.keys()) - set(EXPECTED_PRODUCT_CATEGORIES.keys())
        assert not unexpected, f"Unexpected product categories found: {unexpected}"


# ---------------------------------------------------------------------------
# 4. No duplicates
# ---------------------------------------------------------------------------

class TestNoDuplicates:
    """ISSUE_TRIPLES and PRODUCT_COMBOS must each contain unique entries."""

    def test_no_duplicate_issue_triples(self):
        as_set = set(ISSUE_TRIPLES)
        assert len(as_set) == len(ISSUE_TRIPLES), (
            f"Found {len(ISSUE_TRIPLES) - len(as_set)} duplicate issue triple(s)"
        )

    def test_no_duplicate_product_combos(self):
        as_set = set(PRODUCT_COMBOS)
        assert len(as_set) == len(PRODUCT_COMBOS), (
            f"Found {len(PRODUCT_COMBOS) - len(as_set)} duplicate product combo(s)"
        )


# ---------------------------------------------------------------------------
# 5. Grid generation
# ---------------------------------------------------------------------------

class TestGridGeneration:
    """In-memory grid must have the correct shape, columns, and values."""

    def test_row_count(self):
        assert len(_GRID) == 11_340, (
            f"Expected 11,340 rows, got {len(_GRID)}"
        )

    def test_all_expected_columns_present(self):
        if not _GRID:
            pytest.fail("Grid is empty — cannot check columns")
        actual_columns = set(_GRID[0].keys())
        missing = set(EXPECTED_COLUMNS) - actual_columns
        assert not missing, f"Missing columns: {missing}"

    def test_no_null_values(self):
        for i, row in enumerate(_GRID, start=1):
            for col in EXPECTED_COLUMNS:
                assert row[col] is not None and row[col] != "", (
                    f"Null/empty value in row {i}, column '{col}'"
                )

    def test_ticket_id_is_sequential_from_1(self):
        ids = [row["ticket_id"] for row in _GRID]
        assert ids == list(range(1, 11_341)), (
            "ticket_id is not a gapless sequence from 1 to 11,340"
        )

    def test_customer_sentiment_values(self):
        valid = set(SENTIMENTS)
        bad = {row["customer_sentiment"] for row in _GRID} - valid
        assert not bad, f"Invalid customer_sentiment values: {bad}"

    def test_issue_complexity_values(self):
        valid = set(COMPLEXITIES)
        bad = {row["issue_complexity"] for row in _GRID} - valid
        assert not bad, f"Invalid issue_complexity values: {bad}"

    def test_issue_area_values(self):
        valid = set(EXPECTED_ISSUE_AREAS.keys())
        bad = {row["issue_area"] for row in _GRID} - valid
        assert not bad, f"Invalid issue_area values: {bad}"

    def test_issue_category_values(self):
        valid = {cat for _area, cat, _sub in ISSUE_TRIPLES}
        bad = {row["issue_category"] for row in _GRID} - valid
        assert not bad, f"Invalid issue_category values: {bad}"

    def test_issue_sub_category_values(self):
        valid = {sub for _area, _cat, sub in ISSUE_TRIPLES}
        bad = {row["issue_sub_category"] for row in _GRID} - valid
        assert not bad, f"Invalid issue_sub_category values: {bad}"

    def test_product_category_values(self):
        valid = set(EXPECTED_PRODUCT_CATEGORIES.keys())
        bad = {row["product_category"] for row in _GRID} - valid
        assert not bad, f"Invalid product_category values: {bad}"

    def test_product_sub_category_values(self):
        valid = {sub for _cat, sub in PRODUCT_COMBOS}
        bad = {row["product_sub_category"] for row in _GRID} - valid
        assert not bad, f"Invalid product_sub_category values: {bad}"

    def test_priority_values(self):
        bad = {row["priority"] for row in _GRID} - VALID_PRIORITIES
        assert not bad, f"Invalid priority values: {bad}"


# ---------------------------------------------------------------------------
# 6. Priority distribution (before noise)
# ---------------------------------------------------------------------------

class TestPriorityDistributionBeforeNoise:
    """Every row's deterministic priority must match get_priority(sentiment, complexity)."""

    def test_every_row_priority_matches_get_priority(self):
        mismatches = []
        for i, row in enumerate(_GRID, start=1):
            expected = get_priority(row["customer_sentiment"], row["issue_complexity"])
            if row["priority"] != expected:
                mismatches.append(
                    f"Row {i}: sentiment={row['customer_sentiment']!r}, "
                    f"complexity={row['issue_complexity']!r} → "
                    f"expected {expected!r}, got {row['priority']!r}"
                )
        assert not mismatches, (
            f"{len(mismatches)} priority mismatch(es):\n" + "\n".join(mismatches[:10])
        )

    def test_low_priority_count(self):
        """neutral×less → low: 70 × 18 × 1 = 1,260 rows."""
        low_rows = [r for r in _GRID if r["priority"] == "low"]
        assert len(low_rows) == 1_260, (
            f"Expected 1,260 'low' rows, got {len(low_rows)}"
        )

    def test_medium_priority_count(self):
        """neutral×medium, neutral×high, negative×less → medium: 70 × 18 × 3 = 3,780 rows."""
        medium_rows = [r for r in _GRID if r["priority"] == "medium"]
        assert len(medium_rows) == 3_780, (
            f"Expected 3,780 'medium' rows, got {len(medium_rows)}"
        )

    def test_high_priority_count(self):
        """negative×medium, negative×high, frustrated×less, frustrated×medium → high: 70 × 18 × 4 = 5,040 rows."""
        high_rows = [r for r in _GRID if r["priority"] == "high"]
        assert len(high_rows) == 5_040, (
            f"Expected 5,040 'high' rows, got {len(high_rows)}"
        )

    def test_urgent_priority_count(self):
        """frustrated×high → urgent: 70 × 18 × 1 = 1,260 rows."""
        urgent_rows = [r for r in _GRID if r["priority"] == "urgent"]
        assert len(urgent_rows) == 1_260, (
            f"Expected 1,260 'urgent' rows, got {len(urgent_rows)}"
        )

    def test_priority_counts_sum_to_total(self):
        counts = Counter(row["priority"] for row in _GRID)
        total = sum(counts.values())
        assert total == 11_340, (
            f"Priority counts sum to {total}, expected 11,340"
        )

    @pytest.mark.parametrize("sentiment,complexity", [
        (s, c) for s in SENTIMENTS for c in COMPLEXITIES
    ])
    def test_every_sentiment_complexity_pair_is_represented(self, sentiment, complexity):
        """Every (sentiment, complexity) pair should appear in the grid."""
        matches = [
            r for r in _GRID
            if r["customer_sentiment"] == sentiment
            and r["issue_complexity"] == complexity
        ]
        expected_count = 70 * 18  # one row per issue_triple × product_combo
        assert len(matches) == expected_count, (
            f"({sentiment!r}, {complexity!r}): expected {expected_count} rows, got {len(matches)}"
        )

    def test_apply_noise_changes_some_priorities(self):
        """After apply_noise, some rows change priority but all remain valid."""
        priorities_before = [row["priority"] for row in _GRID]
        priorities_after = apply_noise(priorities_before, noise_rate=0.07, seed=42)

        assert len(priorities_after) == len(priorities_before)

        changed = sum(a != b for a, b in zip(priorities_before, priorities_after))
        assert changed > 0, "apply_noise should have changed at least one priority"

        invalid = set(priorities_after) - VALID_PRIORITIES
        assert not invalid, f"apply_noise produced invalid priority values: {invalid}"
