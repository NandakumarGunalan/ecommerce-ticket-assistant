"""Build the full metadata grid for synthetic ticket generation.

Generates the cross-product of issue triples x product combos x sentiments x complexities,
assigns priorities via the shared priority_rules module, applies ~7% noise, and saves to
synthetic_data/data/grid.csv.

Expected output: 70 x 18 x 3 x 3 = 11,340 rows.
"""

import itertools
import pathlib

import pandas as pd

from synthetic_data.priority_rules import SENTIMENTS, COMPLEXITIES, get_priority, apply_noise

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

ISSUE_TRIPLES = [
    # Order (14)
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
    # Cancellations and returns (14)
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
    # Login and Account (11)
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
    # Shopping (11)
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
    # Shipping (8)
    ("Shipping", "Availability of Faster Delivery Options", "Inability to get some items shipped to a location"),
    ("Shipping", "Availability of Faster Delivery Options", "Unavailability of faster delivery options at a specific location"),
    ("Shipping", "Standard Shipping Speeds and Delivery Charges", "Queries regarding delivery charges"),
    ("Shipping", "Standard Shipping Speeds and Delivery Charges", "Understanding the standard shipping speeds"),
    ("Shipping", "Free Delivery Qualification", "Reasons for an order not qualifying for free delivery"),
    ("Shipping", "Standard Shipping Speeds and Delivery Charges", "Delivery charges applied unexpectedly"),
    ("Shipping", "Shipping Options for Returns", "Availability of faster delivery options (e.g., Same Day & In-a-Day) for return requests"),
    ("Shipping", "Contacting Seller's Partnered Courier Service Providers", "How to get in touch with the courier service providers associated with sellers"),
    # Warranty (12)
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

PRODUCT_COMBOS = [
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
# Grid builder
# ---------------------------------------------------------------------------

def main():
    rows = []
    for issue_triple, product_combo, sentiment, complexity in itertools.product(
        ISSUE_TRIPLES, PRODUCT_COMBOS, SENTIMENTS, COMPLEXITIES
    ):
        issue_area, issue_category, issue_sub_category = issue_triple
        product_category, product_sub_category = product_combo
        rows.append(
            {
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

    df = pd.DataFrame(rows)

    # Apply ~7% noise to the priority column
    df["priority"] = apply_noise(df["priority"].tolist())

    # Assign 1-based ticket IDs
    df.insert(0, "ticket_id", range(1, len(df) + 1))

    # Enforce output column order
    df = df[
        [
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
    ]

    # Save to synthetic_data/data/grid.csv
    output_dir = pathlib.Path(__file__).parent / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "grid.csv"
    df.to_csv(output_path, index=False)

    print(f"Saved {len(df):,} rows to {output_path}")
    assert len(df) == 11_340, f"Expected 11,340 rows, got {len(df)}"


if __name__ == "__main__":
    main()
