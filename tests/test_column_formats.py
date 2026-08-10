"""Column-name coverage across the formats real exports actually use.

Written as a measurement first: before this existed, keyword detection scored
89.9% on these names, and the failures were the informative part. Two were
structural rather than missing vocabulary - English compounds are head-final,
so "invoice dt" was being read as an order id because "invoice" outweighed
"dt", and "of" inverts that ordering, so "date of order" broke the fix.

Kept as a test because vocabulary work has no natural stopping point and it is
otherwise impossible to tell an improvement from a rearrangement. Names come
from accounting packages, Shopify and WooCommerce exports, ERP abbreviations,
Nigerian and UK usage, and hand-typed sheets.

A set of roles as the expectation means "any of these is defensible" - some
names genuinely cannot be resolved from the name alone, and demanding one
answer from Layer 1 would be testing the wrong thing. Layer 2 decides those.
"""

from __future__ import annotations

import pytest

from busylab.detection.keywords import (
    MONETARY_ROLES,
    best_match,
    match_name,
    normalize,
)
from busylab.roles import Role

#: (raw column name, expected role or set of defensible roles)
CASES: list[tuple[str, object]] = [
    # --- DATE -----------------------------------------------------------
    ("Order Date", Role.DATE), ("order_date", Role.DATE),
    ("orderDate", Role.DATE), ("ORDERDATE", Role.DATE),
    ("Date of Order", Role.DATE), ("Txn Date", Role.DATE),
    ("Trans. Date", Role.DATE), ("Doc Date", Role.DATE),
    ("Invoice Dt", Role.DATE), ("DATE_SOLD", Role.DATE),
    ("Sold On", Role.DATE), ("Created At", Role.DATE),
    ("created_date", Role.DATE), ("Paid at", Role.DATE),
    ("Fulfilled at", Role.DATE), ("Week Ending", Role.DATE),
    ("Month", Role.DATE), ("Posting Date", Role.DATE),
    ("Transaction_Timestamp", Role.DATE), ("day", Role.DATE),
    ("Order date (UTC)", Role.DATE), ("sale dt", Role.DATE),
    ("Datum", Role.DATE),

    # --- PRODUCT --------------------------------------------------------
    ("Product", Role.PRODUCT), ("Product Name", Role.PRODUCT),
    ("product_name", Role.PRODUCT), ("productName", Role.PRODUCT),
    ("Item", Role.PRODUCT), ("Item Name", Role.PRODUCT),
    ("ITEM DESCRIPTION", Role.PRODUCT), ("Description", Role.PRODUCT),
    ("Desc", Role.PRODUCT), ("SKU", Role.PRODUCT),
    ("Lineitem name", Role.PRODUCT), ("Stock Code", Role.PRODUCT),
    ("Part No", Role.PRODUCT), ("Article", Role.PRODUCT),
    ("Goods", Role.PRODUCT), ("Model", Role.PRODUCT),
    ("Variant Title", Role.PRODUCT), ("Prod", Role.PRODUCT),
    ("Item_Desc", Role.PRODUCT),

    # --- REVENUE --------------------------------------------------------
    ("Revenue", Role.REVENUE), ("Total Revenue", Role.REVENUE),
    ("Sales", Role.REVENUE), ("Net Sales", Role.REVENUE),
    ("Gross Sales", Role.REVENUE), ("Turnover", Role.REVENUE),
    ("Sales Amount", Role.REVENUE), ("salesamount", Role.REVENUE),
    ("Line Total", Role.REVENUE), ("Amount (NGN)", Role.REVENUE),
    ("Total (₦)", Role.REVENUE), ("Grand Total", Role.REVENUE),
    ("Extended Price", Role.REVENUE), ("Sub Total", Role.REVENUE),
    ("Takings", Role.REVENUE), ("Income", Role.REVENUE),
    ("Total Paid", Role.REVENUE), ("value_sold", Role.REVENUE),
    ("Sales Value", Role.REVENUE), ("Net Amount", Role.REVENUE),

    # --- QUANTITY -------------------------------------------------------
    ("Quantity", Role.QUANTITY), ("Qty", Role.QUANTITY),
    ("QTY SOLD", Role.QUANTITY), ("qty_sold", Role.QUANTITY),
    ("Units", Role.QUANTITY), ("Units Sold", Role.QUANTITY),
    ("No. of Items", Role.QUANTITY), ("Count", Role.QUANTITY),
    ("Lineitem quantity", Role.QUANTITY), ("Volume", Role.QUANTITY),
    ("Pieces", Role.QUANTITY), ("Nos", Role.QUANTITY),
    ("Qnty", Role.QUANTITY),

    # --- UNIT PRICE -----------------------------------------------------
    ("Unit Price", Role.UNIT_PRICE), ("unit_price", Role.UNIT_PRICE),
    ("Price", Role.UNIT_PRICE), ("Price Each", Role.UNIT_PRICE),
    ("Rate", Role.UNIT_PRICE), ("Selling Price", Role.UNIT_PRICE),
    ("Price per Unit", Role.UNIT_PRICE), ("Lineitem price", Role.UNIT_PRICE),
    ("List Price", Role.UNIT_PRICE), ("MRP", Role.UNIT_PRICE),
    ("Unit Cost Price", Role.COST),

    # --- COST -----------------------------------------------------------
    ("Cost", Role.COST), ("COGS", Role.COST),
    ("Cost of Goods", Role.COST), ("Cost of Goods Sold", Role.COST),
    ("Unit Cost", Role.COST), ("Buying Price", Role.COST),
    ("Purchase Price", Role.COST), ("Cost Price", Role.COST),
    ("Landed Cost", Role.COST), ("total_cost", Role.COST),
    ("Supplier Cost", Role.COST),

    # --- CUSTOMER -------------------------------------------------------
    ("Customer ID", Role.CUSTOMER_ID), ("customer_id", Role.CUSTOMER_ID),
    ("CustomerID", Role.CUSTOMER_ID), ("Cust ID", Role.CUSTOMER_ID),
    ("Customer", Role.CUSTOMER_ID), ("Customer Name", Role.CUSTOMER_ID),
    ("Client", Role.CUSTOMER_ID), ("Buyer", Role.CUSTOMER_ID),
    ("Account", Role.CUSTOMER_ID), ("Email", Role.CUSTOMER_ID),
    ("Billing Name", Role.CUSTOMER_ID), ("Phone", Role.CUSTOMER_ID),
    ("Member No", Role.CUSTOMER_ID),

    # --- ORDER ----------------------------------------------------------
    ("Order ID", Role.ORDER_ID), ("order_id", Role.ORDER_ID),
    ("Order No", Role.ORDER_ID), ("Order Number", Role.ORDER_ID),
    ("Invoice No", Role.ORDER_ID), ("Invoice Number", Role.ORDER_ID),
    ("Receipt No", Role.ORDER_ID), ("Transaction ID", Role.ORDER_ID),
    ("Name", {Role.ORDER_ID, Role.PRODUCT, Role.CUSTOMER_ID}),  # Shopify uses it for the order
    ("Ref", Role.ORDER_ID), ("Docket No", Role.ORDER_ID),
    ("Bill No", Role.ORDER_ID),

    # --- CHANNEL --------------------------------------------------------
    ("Channel", Role.CHANNEL), ("Sales Channel", Role.CHANNEL),
    ("sales_channel", Role.CHANNEL), ("Source", Role.CHANNEL),
    ("Platform", Role.CHANNEL), ("Store", {Role.CHANNEL, Role.REGION}),
    ("Outlet", {Role.CHANNEL, Role.REGION}), ("Order Source", Role.CHANNEL),
    ("Medium", Role.CHANNEL),

    # --- REGION ---------------------------------------------------------
    ("Region", Role.REGION), ("State", Role.REGION),
    ("City", Role.REGION), ("Location", Role.REGION),
    ("Branch", Role.REGION), ("Territory", Role.REGION),
    ("Shipping City", Role.REGION), ("Country", Role.REGION),
    ("Area", Role.REGION), ("Zone", Role.REGION),
    ("LGA", Role.REGION), ("Postcode", Role.REGION),

    # --- DISCOUNT -------------------------------------------------------
    ("Discount", Role.DISCOUNT), ("Discount Amount", Role.DISCOUNT),
    ("discount_amount", Role.DISCOUNT), ("Disc", Role.DISCOUNT),
    ("Discount %", Role.DISCOUNT), ("Rebate", Role.DISCOUNT),
    ("Promo", Role.DISCOUNT), ("Markdown", Role.DISCOUNT),
    ("Lineitem discount", Role.DISCOUNT),

    # --- CATEGORY -------------------------------------------------------
    ("Category", Role.CATEGORY), ("Product Category", Role.CATEGORY),
    ("Type", Role.CATEGORY), ("Product Type", Role.CATEGORY),
    ("Class", Role.CATEGORY), ("Group", Role.CATEGORY),
    ("Department", Role.CATEGORY), ("Segment", Role.CATEGORY),
    ("Brand", Role.CATEGORY), ("Sub Category", Role.CATEGORY),

    # --- PAYMENT --------------------------------------------------------
    ("Payment Method", Role.PAYMENT_METHOD), ("payment_method", Role.PAYMENT_METHOD),
    ("Payment", Role.PAYMENT_METHOD), ("Paid By", Role.PAYMENT_METHOD),
    ("Tender", Role.PAYMENT_METHOD), ("Pay Mode", Role.PAYMENT_METHOD),
    ("Payment Type", Role.PAYMENT_METHOD),
]


def _allowed(expected: object) -> set:
    return expected if isinstance(expected, set) else {expected}


@pytest.mark.parametrize("raw,expected", CASES, ids=[c[0] for c in CASES])
def test_column_name_resolves_to_its_role(raw, expected):
    match = best_match(raw)
    assert match is not None, f"{raw!r} proposed no role at all"
    allowed = _allowed(expected)
    assert match.role in allowed, (
        f"{raw!r} -> {match.role.value} via {match.term!r}/{match.quality}, "
        f"expected one of {sorted(r.value for r in allowed)}"
    )


def test_overall_accuracy_does_not_regress():
    """A floor, so a vocabulary change cannot quietly trade one name for another."""
    correct = 0
    for raw, expected in CASES:
        match = best_match(raw)
        if match is not None and match.role in _allowed(expected):
            correct += 1
    assert correct == len(CASES), f"{correct}/{len(CASES)} - see the failures above"


class TestNormalisation:
    """The formats a column name arrives in."""

    @pytest.mark.parametrize(
        "raw",
        ["Order Date", "order_date", "orderDate", "ORDER DATE", "Order-Date",
         "  Order   Date  ", "Order.Date", "order date "],
    )
    def test_every_separator_style_normalises_alike(self, raw):
        assert normalize(raw) == "order date"

    def test_currency_symbols_do_not_become_words(self):
        assert normalize("Total ($)") == "total"
        assert normalize("Amount (£)") == "amount"

    def test_the_naira_sign_is_kept_as_a_word(self):
        # A column called "Total (N)" should still read as money.
        assert "naira" in normalize("Total (₦)")

    def test_trailing_annotations_survive(self):
        assert normalize("Order date (UTC)") == "order date utc"


class TestHeadFinalRule:
    """English compounds are head-final, and the scoring has to know it."""

    def test_the_last_word_decides(self):
        # "invoice date" is a kind of date, not a kind of invoice.
        assert best_match("Invoice Dt").role is Role.DATE
        assert best_match("Invoice No").role is Role.ORDER_ID

    def test_a_leading_qualifier_does_not_win(self):
        # The case spec 3.2 singles out as the dangerous one.
        assert best_match("discount_amount").role is Role.DISCOUNT
        assert best_match("Discount Amount").role is Role.DISCOUNT

    def test_of_inverts_the_ordering(self):
        # "date of order" is a date; a naive tail rule reads it as an order.
        assert best_match("Date of Order").role is Role.DATE
        assert best_match("Cost of Goods").role is Role.COST
        assert best_match("No. of Items").role is Role.QUANTITY

    def test_tail_matches_outrank_head_matches(self):
        # Not a phrase in the dictionary, so the tail rule has to carry it.
        tail = best_match("Batch Dt")
        assert tail.role is Role.DATE
        assert tail.quality == "phrase_tail"


class TestNonMeasureColumns:
    """Amounts that sit beside a sale without being one.

    These profile identically to a real cost column - positive, continuous, two
    decimals, smaller than revenue - so content cannot separate them. Proposing
    nothing is the only safe answer, because a shipping fee accepted as cost of
    goods changes every profit number silently.
    """

    @pytest.mark.parametrize(
        "raw",
        ["tax_amount", "VAT", "Tax", "shipping_fee", "Shipping", "Freight",
         "Delivery Charge", "Service Charge", "Handling Fee", "Commission",
         "Refund Amount", "Tip", "Gratuity", "Duty", "amount of tax",
         "cost of shipping", "Chargeback", "Rounding"],
    )
    def test_no_monetary_role_is_proposed(self, raw):
        # The guarantee is about the money roles specifically. A stray weak
        # proposal elsewhere - "ref" matching inside "refund" - is noise that
        # Layer 2 discards, and asserting an empty list would be claiming more
        # than the suppression actually promises.
        proposed = {m.role for m in match_name(raw)}
        leaked = proposed & MONETARY_ROLES
        assert not leaked, f"{raw!r} proposed {sorted(r.value for r in leaked)}"

    @pytest.mark.parametrize(
        "raw",
        ["tax_amount", "VAT", "shipping_fee", "Freight", "Delivery Charge",
         "Commission", "Refund Amount", "Rounding"],
    )
    def test_nothing_is_proposed_confidently(self, raw):
        # Whatever survives must be too weak to be assigned without a human,
        # which is what keeps these columns out of the analysis.
        for match in match_name(raw):
            assert match.score < 0.3, f"{raw!r} -> {match.role.value} at {match.score}"

    def test_a_location_is_not_swallowed_by_the_suppression(self):
        # "Shipping City" starts with a non-measure word and is still a region.
        # Suppressing the whole name would lose it.
        assert best_match("Shipping City").role is Role.REGION
        assert best_match("Delivery State").role is Role.REGION

    @pytest.mark.parametrize(
        "raw,role",
        [("Cost Price", Role.COST), ("COGS", Role.COST),
         ("Total Revenue", Role.REVENUE), ("Discount Amount", Role.DISCOUNT),
         ("Unit Cost", Role.COST)],
    )
    def test_real_measures_are_untouched(self, raw, role):
        # The suppression must not swallow the columns that matter.
        assert best_match(raw).role is role
