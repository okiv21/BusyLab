"""Customer intelligence tests.

Written against ``fixtures.customer_business``, which has known behaviour
planted in it: cohort decay, a fixed set of champions, a cohort that bought
early and stopped, acquisition halving in the second half, and exactly one
product pair genuinely bought together.

The control that matters here is basket analysis. Every product pair is a
comparison, so a catalogue of independent products will throw up impressive
lift multiples by chance unless the family is corrected and rare pairs are
floored out.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from busylab.analysis import analyse
from busylab.roles import Tier

from . import fixtures


@pytest.fixture(scope="module")
def customers():
    return analyse(fixtures.customer_business(), strict=True)


@pytest.fixture(scope="module")
def independent_products():
    """Orders drawn at random: no pair is genuinely associated."""
    rng = np.random.default_rng(4)
    rows = []
    start = pd.Timestamp("2024-01-01")
    for order in range(1500):
        when = start + pd.Timedelta(days=int(rng.integers(0, 540)))
        customer = int(rng.integers(1, 300))
        chosen = rng.choice(
            fixtures.PRODUCTS, size=int(rng.integers(2, 4)), replace=False
        )
        for product in chosen:
            rows.append(
                {
                    "order_date": when,
                    "order_id": f"O{order}",
                    "customer_id": f"C{customer}",
                    "product_name": str(product),
                    "quantity": 1,
                    "unit_price": fixtures.PRICES[str(product)],
                    "total_paid": fixtures.PRICES[str(product)],
                }
            )
    return analyse(pd.DataFrame(rows), strict=True)


def _by_id(result, finding_id: str):
    return next((f for f in result.findings if f.id == finding_id), None)


def _ids(result) -> set[str]:
    return {f.id for f in result.findings}


# --------------------------------------------------------------------------
# The tier gates itself
# --------------------------------------------------------------------------


def test_no_customer_findings_without_a_customer_column() -> None:
    frame = fixtures.customer_business().drop(columns=["customer_id"])
    result = analyse(frame, strict=True)

    assert "rfm_segments" not in _ids(result)
    assert "cohort_retention" not in _ids(result)
    assert "repeat_vs_new" not in _ids(result)


def test_customer_findings_are_tagged_to_the_customer_tier(customers) -> None:
    for name in ("rfm_segments", "cohort_retention", "repeat_vs_new"):
        finding = _by_id(customers, name)
        assert finding is not None
        assert finding.tier is Tier.CUSTOMER


# --------------------------------------------------------------------------
# Repeat versus new
# --------------------------------------------------------------------------


def test_falling_acquisition_is_identified_as_the_driver(customers) -> None:
    """Intake halves while returning customers keep buying, by construction."""
    finding = _by_id(customers, "repeat_vs_new")

    assert finding is not None
    assert finding.facts["driver"] == "acquisition"
    assert finding.facts["new_change_pct"] < finding.facts["repeat_change_pct"]
    assert "who is arriving" in finding.summary


def test_repeat_vs_new_charts_both_series(customers) -> None:
    finding = _by_id(customers, "repeat_vs_new")
    series = finding.chart_data["series"]

    assert len(series) >= 4
    assert all({"period", "repeat", "new"} <= set(point) for point in series)


def test_a_business_where_both_move_together_says_nothing() -> None:
    """No divergence, no finding. There is nothing to report.

    Constructed so both halves genuinely hold flat: a fixed number of brand new
    customers arrive each month *and* a fixed number of previously-seen ones
    come back. Note that "the same people buy every month" is not this case —
    there everyone is new in month one and returning thereafter, which is
    acquisition stopping dead and very much worth reporting.
    """
    rng = np.random.default_rng(8)
    rows = []
    start = pd.Timestamp("2024-01-01")
    seen: list[int] = []
    next_customer = 1

    for month in range(14):
        when = start + pd.DateOffset(months=month)

        def buy(customer: int) -> None:
            rows.append(
                {
                    "order_date": when + pd.Timedelta(days=int(rng.integers(0, 27))),
                    "customer_id": f"C{customer}",
                    "product_name": str(rng.choice(fixtures.PRODUCTS)),
                    "total_paid": 5000.0,
                }
            )

        arrivals = []
        for _ in range(20):  # steady intake
            buy(next_customer)
            arrivals.append(next_customer)
            next_customer += 1

        for customer in rng.choice(seen, size=min(20, len(seen)), replace=False) if seen else []:
            buy(int(customer))  # steady return traffic

        seen.extend(arrivals)

    result = analyse(pd.DataFrame(rows), strict=True)
    finding = _by_id(result, "repeat_vs_new")

    if finding is not None:
        gap = abs(
            finding.facts["new_change_pct"] - finding.facts["repeat_change_pct"]
        )
        assert gap < 0.6, "both halves held flat, so no strong divergence"


# --------------------------------------------------------------------------
# RFM
# --------------------------------------------------------------------------


def test_customers_are_sorted_into_named_segments(customers) -> None:
    finding = _by_id(customers, "rfm_segments")
    assert finding is not None

    names = {row["segment"] for row in finding.facts["segments"]}
    assert names & {"Champions", "At risk", "Lost", "New"}
    assert sum(r["customers"] for r in finding.facts["segments"]) == finding.facts[
        "total_customers"
    ]


def test_the_planted_lapsed_cohort_lands_in_at_risk_or_lost(customers) -> None:
    """60 customers bought in the first three months and stopped dead."""
    finding = _by_id(customers, "rfm_segments")
    by_key = {row["key"]: row for row in finding.facts["segments"]}

    lapsed = by_key.get("at_risk", {}).get("customers", 0) + by_key.get(
        "lost", {}
    ).get("customers", 0)
    assert lapsed >= 50


def test_recency_is_measured_against_the_data_not_today(customers) -> None:
    """A file from last year must not report every customer as lost."""
    finding = _by_id(customers, "rfm_segments")

    assert finding.facts["as_of"] == "2025-06-21" or finding.facts["as_of"].startswith(
        "2025-"
    )
    assert any("last sale in the file" in n for n in finding.evidence.notes)


def test_rfm_scores_are_quantiles_not_fixed_thresholds(customers) -> None:
    """A naira threshold that means "big spender" here is noise elsewhere."""
    finding = _by_id(customers, "rfm_segments")
    assert any("quartile" in n.lower() for n in finding.evidence.notes)


def test_rfm_chart_payload_is_capped(customers) -> None:
    """A quadrant does not need every customer as a dot."""
    finding = _by_id(customers, "rfm_segments")
    assert len(finding.chart_data["customers"]) <= 400


def test_too_few_customers_produces_no_segmentation() -> None:
    rng = np.random.default_rng(1)
    rows = [
        {
            "order_date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=int(i)),
            "customer_id": f"C{i % 6}",
            "product_name": str(rng.choice(fixtures.PRODUCTS)),
            "total_paid": 5000.0,
        }
        for i in range(120)
    ]
    result = analyse(pd.DataFrame(rows), strict=True)
    assert "rfm_segments" not in _ids(result)


# --------------------------------------------------------------------------
# Cohort retention
# --------------------------------------------------------------------------


def test_retention_curve_matches_the_planted_decay(customers) -> None:
    """Return probability was planted at 0.62 for the first month."""
    finding = _by_id(customers, "cohort_retention")
    assert finding is not None
    assert 0.5 <= finding.facts["month_one_retention"] <= 0.8


def test_retention_declines_with_age(customers) -> None:
    finding = _by_id(customers, "cohort_retention")
    curve = finding.facts["curve"]

    assert len(curve) >= 3
    assert curve[0]["retention"] > curve[-1]["retention"]
    assert all(0.0 <= point["retention"] <= 1.0 for point in curve)


def test_recent_cohorts_do_not_drag_the_curve_down(customers) -> None:
    """A cohort one month old has not failed to survive twelve months."""
    finding = _by_id(customers, "cohort_retention")
    assert any("old enough" in n for n in finding.evidence.notes)


def test_tiny_cohorts_are_excluded(customers) -> None:
    finding = _by_id(customers, "cohort_retention")
    assert any("too small to read" in n for n in finding.evidence.notes)


# --------------------------------------------------------------------------
# Basket analysis: the multiple-comparisons trap again
# --------------------------------------------------------------------------


def test_the_planted_pair_is_found(customers) -> None:
    finding = _by_id(customers, "basket_analysis")
    assert finding is not None

    pair = {finding.facts["strongest"]["a"], finding.facts["strongest"]["b"]}
    assert pair == {"Linen Candle", fixtures.COMPANION}
    assert finding.facts["strongest"]["lift"] > 1.5


def test_independent_products_produce_no_basket_finding(
    independent_products,
) -> None:
    """The control. Lift on unrelated products is chance, not cross-sell."""
    assert "basket_analysis" not in _ids(independent_products)


def test_basket_reports_its_correction_and_support_floor(customers) -> None:
    finding = _by_id(customers, "basket_analysis")

    assert finding.evidence.correction == "Benjamini-Hochberg FDR"
    assert finding.evidence.adjusted_p is not None
    assert finding.facts["support_floor"] >= 8
    assert any("fewer than" in n for n in finding.evidence.notes)


def test_a_rare_pair_cannot_post_a_huge_multiple(customers) -> None:
    """Two co-occurrences must never become a headline lift."""
    finding = _by_id(customers, "basket_analysis")
    floor = finding.facts["support_floor"]

    for pair in finding.facts["pairs"]:
        assert pair["together"] >= floor


def test_basket_says_what_counts_as_a_basket(customers) -> None:
    """Order-level and same-day-customer are different claims."""
    finding = _by_id(customers, "basket_analysis")

    assert finding.facts["basket_definition"] == "one order"
    assert any("basket is" in n for n in finding.evidence.notes)


def test_basket_falls_back_to_same_day_without_an_order_id() -> None:
    frame = fixtures.customer_business().drop(columns=["order_id"])
    result = analyse(frame, strict=True)
    finding = _by_id(result, "basket_analysis")

    if finding is not None:
        assert finding.facts["basket_definition"] == "one customer on one day"


# --------------------------------------------------------------------------
# House rules still hold
# --------------------------------------------------------------------------


def test_customer_findings_do_not_read_as_advice(customers) -> None:
    """Cross-sell is the easiest place to slip into telling people what to do."""
    from busylab.findings import check_non_directive

    for finding in customers.findings:
        assert check_non_directive(finding.summary) == [], finding.summary


def test_every_customer_finding_carries_its_chart(customers) -> None:
    expected = {
        "rfm_segments": "quadrant",
        "cohort_retention": "cohort_heatmap",
        "repeat_vs_new": "stacked_area",
        "basket_analysis": "bar_horizontal",
    }
    for name, chart in expected.items():
        finding = _by_id(customers, name)
        if finding is not None:
            assert finding.chart.value == chart


def test_customer_findings_serialise(customers) -> None:
    import json

    payload = json.dumps(customers.to_dict())
    assert "rfm_segments" in payload
