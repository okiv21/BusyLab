"""Detection tests.

The behaviours asserted here are the promises in spec 3.2 and 3.3:
a clean file asks nothing, a messy file asks only about its own mess, content
overrules a confident but wrong name, and nothing is silently mislabelled.
"""

from __future__ import annotations

import pandas as pd
import pytest

from busylab import loading
from busylab.detection import detect, schema_fingerprint
from busylab.detection.content import profile_column, score_content
from busylab.detection.keywords import match_name
from busylab.roles import Role, Tier

from . import fixtures


# --------------------------------------------------------------------------
# Layer 1: keywords propose
# --------------------------------------------------------------------------


def _top_role(name: str) -> Role | None:
    matches = match_name(name)
    return matches[0].role if matches else None


@pytest.mark.parametrize(
    "name,expected",
    [
        ("order_date", Role.DATE),
        ("orderDate", Role.DATE),
        ("Order Date", Role.DATE),
        ("product_name", Role.PRODUCT),
        ("Item Descrption", Role.PRODUCT),  # misspelled
        ("total_paid", Role.REVENUE),
        ("qty", Role.QUANTITY),
        ("no of units", Role.QUANTITY),
        ("unit_cst", Role.COST),  # misspelled
        ("COGS", Role.COST),
        ("Selling Price", Role.UNIT_PRICE),
        ("cust id", Role.CUSTOMER_ID),
        ("src", Role.CHANNEL),
        ("LGA", Role.REGION),  # Nigerian local context
        ("payment method", Role.PAYMENT_METHOD),
    ],
)
def test_keywords_propose_the_obvious(name: str, expected: Role) -> None:
    assert _top_role(name) is expected


def test_discount_amount_is_not_revenue() -> None:
    """The sharpest silent failure in the spec: a discount read as revenue."""
    scores = {m.role: m.score for m in match_name("discount_amount")}
    assert scores[Role.DISCOUNT] > scores.get(Role.REVENUE, 0.0)


def test_generic_money_words_stay_weak() -> None:
    """"amount" alone must not be confident enough to decide anything."""
    scores = {m.role: m.score for m in match_name("amount")}
    assert scores[Role.REVENUE] < 0.5


def test_unrecognised_name_proposes_nothing() -> None:
    assert match_name("Column3") == []


# --------------------------------------------------------------------------
# Layer 2: content verifies and rescues
# --------------------------------------------------------------------------


def test_content_rescues_a_badly_named_date_column() -> None:
    values = pd.Series(pd.date_range("2025-01-01", periods=200).strftime("%d/%m/%Y"))
    scores = score_content(profile_column(values, "Column3"))
    assert scores[Role.DATE] > 0.9


def test_content_vetoes_a_date_column_holding_places() -> None:
    """Spec 3.2's own example: named date, contains "Lagos, Ikeja"."""
    values = pd.Series(["Lagos, Ikeja", "Abuja", "Kano"] * 60)
    scores = score_content(profile_column(values, "date"))
    assert scores.get(Role.DATE, 0.0) == 0.0


def test_currency_symbols_inside_numeric_cells_still_parse() -> None:
    values = pd.Series(["N1,234.50", "(500)", "2,000", " 3450 ", "n/a"] * 40)
    profile = profile_column(values, "Amount")
    assert profile.numeric_rate == 1.0


def test_percentage_discounts_are_recognised() -> None:
    values = pd.Series([f"{v}%" for v in range(0, 30)] * 10)
    scores = score_content(profile_column(values, "disc"))
    assert scores[Role.DISCOUNT] > 0.9


def test_identifiers_must_be_whole_numbers() -> None:
    """A column of decimals is a price, never an order number."""
    prices = pd.Series([1234.56 + i for i in range(300)])
    scores = score_content(profile_column(prices, "x"))
    assert scores.get(Role.ORDER_ID, 0.0) < 0.2


# --------------------------------------------------------------------------
# Layer 3: the user confirms only the ambiguous
# --------------------------------------------------------------------------


def test_clean_file_asks_nothing() -> None:
    """The central promise: a clean file sails through with zero questions."""
    result = detect(fixtures.base_sales())
    assert result.prompts == []
    assert result.ready
    assert result.missing == set()


def test_clean_file_maps_every_role_correctly() -> None:
    result = detect(fixtures.base_sales())
    assert result.assignments[Role.DATE] == "order_date"
    assert result.assignments[Role.PRODUCT] == "product_name"
    assert result.assignments[Role.REVENUE] == "total_paid"
    assert result.assignments[Role.QUANTITY] == "quantity"
    assert result.assignments[Role.UNIT_PRICE] == "unit_price"
    assert result.assignments[Role.COST] == "unit_cost"
    assert result.assignments[Role.CUSTOMER_ID] == "customer_id"
    assert result.assignments[Role.CHANNEL] == "channel"


def test_clean_file_unlocks_every_tier() -> None:
    result = detect(fixtures.base_sales())
    assert result.tiers[Tier.CORE]
    assert result.tiers[Tier.MARGIN]
    assert result.tiers[Tier.CUSTOMER]
    assert result.tiers[Tier.SEGMENT]


def test_messy_file_still_finds_the_required_roles() -> None:
    result = detect(fixtures.messy_frame())
    assert result.assignments[Role.DATE] == "Column3"
    assert result.assignments[Role.PRODUCT] == "Item Descrption"
    assert result.assignments[Role.REVENUE] == "Amount (NGN)"
    assert result.missing == set()


def test_messy_file_does_not_mistake_discount_for_revenue() -> None:
    result = detect(fixtures.messy_frame())
    assert result.assignments[Role.DISCOUNT] == "discount_amount"
    assert result.assignments[Role.REVENUE] != "discount_amount"


def test_conflicting_column_is_raised_not_guessed() -> None:
    """A column named date holding places must be asked about, not reassigned."""
    result = detect(fixtures.messy_frame())
    assert result.role_of("date") is None
    assert any(p.column == "date" for p in result.prompts)


def test_unknown_categorical_is_offered_as_a_grouping() -> None:
    """Spec 3.3's middle path, rather than silently ignoring the column."""
    result = detect(fixtures.messy_frame())
    prompt = next(p for p in result.prompts if p.column == "salesperson")
    assert prompt.allow_group_by


def test_user_effort_scales_with_their_own_mess() -> None:
    clean = detect(fixtures.base_sales())
    messy = detect(fixtures.messy_frame())
    assert len(clean.prompts) == 0
    assert len(messy.prompts) <= 3


def test_overrides_settle_a_question_for_good() -> None:
    frame = fixtures.messy_frame()
    result = detect(frame, overrides={"salesperson": Role.GROUP_BY})
    assert not any(p.column == "salesperson" for p in result.prompts)


def test_locked_tiers_explain_what_would_unlock_them() -> None:
    frame = fixtures.base_sales().drop(columns=["customer_id", "unit_cost"])
    result = detect(frame)
    locked = dict(result.locked_tiers())
    assert Tier.CUSTOMER in locked
    assert Tier.MARGIN in locked
    assert "cost" in locked[Tier.MARGIN].lower()


def test_missing_required_role_blocks_readiness() -> None:
    frame = fixtures.base_sales().drop(
        columns=["total_paid", "unit_price", "quantity"]
    )
    result = detect(frame)
    assert Role.REVENUE in result.missing
    assert not result.ready


def test_revenue_can_be_derived_from_quantity_and_price() -> None:
    frame = fixtures.base_sales().drop(columns=["total_paid"])
    result = detect(frame)
    assert result.missing == set()
    assert any("quantity x unit price" in n for n in result.notes)


def test_cost_basis_is_resolved_arithmetically() -> None:
    """Per-unit vs line-total cost inverts every margin, so it must be pinned."""
    result = detect(fixtures.base_sales())
    assert any("per-unit" in n for n in result.notes)


def test_line_total_cost_is_told_apart_from_per_unit_cost() -> None:
    frame = fixtures.base_sales()
    frame["unit_cost"] = frame["unit_cost"] * frame["quantity"]  # now a line total
    result = detect(frame)
    assert any("line total" in n for n in result.notes)
    assert not any("per-unit" in n for n in result.notes)


def test_arithmetic_checks_compare_rows_not_medians() -> None:
    """median(qty) x median(price) is not median(revenue).

    Comparing column medians reports a perfectly consistent file as
    inconsistent as soon as quantity varies, which is always.
    """
    frame = fixtures.base_sales()
    assert (frame["quantity"] * frame["unit_price"]).equals(frame["total_paid"])
    result = detect(frame)
    assert any("agrees with quantity" in n for n in result.notes)


def test_inconsistent_revenue_is_reported_as_such() -> None:
    frame = fixtures.base_sales()
    frame["total_paid"] = frame["total_paid"] * 3  # no longer qty x price
    result = detect(frame)
    assert any("does not equal" in n for n in result.notes)


def test_numbers_held_as_text_are_not_parsed_as_dates() -> None:
    """A money column read from Excel as object dtype is not a date column.

    pandas will happily read "6500.0" as the year 6500, which invents a date
    column and overflows on date arithmetic. Only caught by round-tripping
    through a real file, so it is pinned here.
    """
    values = pd.Series([f"{v}.0" for v in range(6000, 6300)], dtype="object")
    profile = profile_column(values, "total_paid")
    assert profile.numeric_rate == 1.0
    assert profile.datetime_rate == 0.0


def test_full_pipeline_survives_an_excel_round_trip(tmp_path) -> None:
    """Detection must not crash on a file that has been through Excel."""
    path = tmp_path / "round_trip.xlsx"
    fixtures.base_sales(n=200).to_excel(path, index=False)
    frame, _ = loading.load(path)
    result = detect(frame)

    assert result.missing == set()
    assert result.assignments[Role.DATE] == "order_date"
    assert result.assignments[Role.REVENUE] == "total_paid"


def test_loader_provenance_column_is_never_a_question() -> None:
    frame = fixtures.base_sales()
    frame["_sheet"] = "Jan"
    result = detect(frame)
    assert not any(p.column == "_sheet" for p in result.prompts)


# --------------------------------------------------------------------------
# Mapping memory (spec 4.1)
# --------------------------------------------------------------------------


def test_fingerprint_is_stable_across_refreshes() -> None:
    a = schema_fingerprint(fixtures.base_sales(n=200, seed=1))
    b = schema_fingerprint(fixtures.base_sales(n=350, seed=2))
    assert a == b, "same schema, more rows: must run silently"


def test_fingerprint_changes_when_a_column_is_added() -> None:
    frame = fixtures.base_sales()
    drifted = frame.assign(region="Lagos")
    assert schema_fingerprint(frame) != schema_fingerprint(drifted)


def test_fingerprint_changes_when_a_column_is_renamed() -> None:
    frame = fixtures.base_sales()
    drifted = frame.rename(columns={"total_paid": "grand_total"})
    assert schema_fingerprint(frame) != schema_fingerprint(drifted)


# --------------------------------------------------------------------------
# Structural mess: the loader (spec 3.1)
# --------------------------------------------------------------------------


def test_loader_survives_banner_merged_header_and_total_rows(tmp_path) -> None:
    path = fixtures.write_messy_workbook(tmp_path / "messy.xlsx", n=120)
    frame, report = loading.load(path)

    assert report.dropped_total_rows >= 4
    assert len(frame) == 120, "every transaction kept, every subtotal removed"
    assert not any("total" in str(v).lower() for v in frame.iloc[:, 0])


def test_loaded_messy_workbook_detects_cleanly(tmp_path) -> None:
    path = fixtures.write_messy_workbook(tmp_path / "messy.xlsx", n=120)
    frame, _ = loading.load(path)
    result = detect(frame)

    assert result.missing == set()
    assert result.assignments[Role.DATE] == "Date"
    assert result.assignments[Role.PRODUCT] == "Product"


def test_monthly_tabs_combine_into_one_table(tmp_path) -> None:
    """A year split across twelve tabs is one dataset, not twelve."""
    path = fixtures.write_monthly_workbook(tmp_path / "monthly.xlsx", months=6)
    frame, report = loading.load(path)

    assert len(report.sheets_used) == 6
    assert len(frame) == 181  # Jan-Jun 2025
    assert "_sheet" in frame.columns

    result = detect(frame)
    assert result.assignments[Role.DATE] == "order_date"


def test_clean_workbook_round_trips_with_no_questions(tmp_path) -> None:
    path = fixtures.write_clean_workbook(tmp_path / "clean.xlsx")
    frame, report = loading.load(path)
    result = detect(frame)

    assert report.dropped_total_rows == 0
    assert result.prompts == []
    assert result.ready
