"""Tests for the analyses that need only date, product, quantity and price.

Each is tested against data with the pattern deliberately planted, and against
data where it is deliberately absent. The second half matters more: these were
added because the story was thin, and the temptation when adding analyses to
thicken a story is to let them fire on nothing.

Worth recording why they were needed. On three real files the engine produced
almost nothing but comparisons of the form "the biggest against the smallest",
partly because those files carried no cost and no customer id so two whole
pillars never ran, and partly because the core pillar really was thin.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from busylab.analysis.dataset import build
from busylab.analysis.patterns import (
    DORMANT_PERIODS,
    concentration_classes,
    lifecycle,
    order_spread,
    price_response,
)
from busylab.detection import detect
from busylab.findings import FindingType, Severity


def frame_from(rows: list[dict]):
    raw = pd.DataFrame(rows)
    return build(raw, detect(raw))


def sales(
    *,
    months: int = 14,
    products: dict[str, tuple[float, int]] | None = None,
    seed: int = 5,
):
    """A plain monthly file: each product at a steady price and volume."""
    products = products or {"Rice": (5000.0, 40), "Beans": (3000.0, 30)}
    rng = np.random.default_rng(seed)
    rows = []
    order = 0
    for month in pd.date_range("2025-01-01", periods=months, freq="MS"):
        for name, (price, units) in products.items():
            for _ in range(4):
                order += 1
                rows.append(
                    {
                        "Order Date": month + pd.Timedelta(days=int(rng.integers(0, 27))),
                        "Order ID": f"ORD-{order}",
                        "Product": name,
                        "Quantity": max(1, int(units / 4 + rng.normal(0, 1))),
                        "Unit Price": round(price * (1 + rng.normal(0, 0.01)), 2),
                    }
                )
    return rows


class TestConcentrationClasses:
    """How few products the business runs on, which a ranking does not answer."""

    def test_a_narrow_base_is_reported_with_a_count(self):
        # One product at 90%, nine sharing the rest.
        products = {"Star": (100_000.0, 40)}
        products.update({f"Minor{i}": (500.0, 4) for i in range(9)})
        findings = concentration_classes(frame_from(sales(products=products)))
        assert findings, "a business running on one product said nothing"
        summary = findings[0].summary
        assert "products bring in" in summary
        assert findings[0].facts["vital_count"] <= 2

    def test_a_narrow_base_is_worth_attention(self):
        products = {"Star": (100_000.0, 40)}
        products.update({f"Minor{i}": (500.0, 4) for i in range(9)})
        assert concentration_classes(
            frame_from(sales(products=products))
        )[0].severity is Severity.WATCH

    def test_an_even_spread_is_reported_without_alarm(self):
        products = {f"P{i}": (5000.0, 30) for i in range(8)}
        finding = concentration_classes(frame_from(sales(products=products)))[0]
        assert finding.severity is Severity.NEUTRAL

    def test_the_tail_is_counted(self):
        products = {"Big": (200_000.0, 50), "Also": (150_000.0, 40)}
        products.update({f"Crumb{i}": (100.0, 1) for i in range(6)})
        facts = concentration_classes(frame_from(sales(products=products)))[0].facts
        assert facts["tail_count"] >= 4

    def test_too_few_products_to_be_worth_saying(self):
        # Two products is not a concentration story, it is a list.
        assert concentration_classes(
            frame_from(sales(products={"A": (100.0, 5), "B": (100.0, 5)}))
        ) == []


class TestPriceResponse:
    """Whether charging more sold fewer, and whether the trade paid."""

    def _with_price_moves(self, elasticity: float, seed: int = 11):
        """Build a file where units really do respond to price by a known amount."""
        rng = np.random.default_rng(seed)
        rows = []
        order = 0
        base_price, base_units = 1000.0, 100.0
        for i, month in enumerate(pd.date_range("2025-01-01", periods=14, freq="MS")):
            # Price swings well beyond the noise floor, alternating.
            price = base_price * (1 + 0.25 * np.sin(i))
            units = base_units * (price / base_price) ** elasticity
            for _ in range(4):
                order += 1
                rows.append(
                    {
                        "Order Date": month + pd.Timedelta(days=int(rng.integers(0, 27))),
                        "Order ID": f"ORD-{order}",
                        "Product": "Rice",
                        "Quantity": max(1, int(units / 4)),
                        "Unit Price": round(price, 2),
                    }
                )
            # A second product so the family correction has something to do.
            for _ in range(2):
                order += 1
                rows.append(
                    {
                        "Order Date": month + pd.Timedelta(days=int(rng.integers(0, 27))),
                        "Order ID": f"ORD-{order}",
                        "Product": "Beans",
                        "Quantity": 20,
                        "Unit Price": 500.0,
                    }
                )
        return frame_from(rows)

    def test_a_strong_response_is_found(self):
        findings = price_response(self._with_price_moves(-2.0))
        assert findings, "a planted price response was not detected"
        assert findings[0].type is FindingType.ELASTICITY
        assert findings[0].facts["elasticity"] < -1

    def test_the_direction_is_stated_plainly(self):
        finding = price_response(self._with_price_moves(-2.0))[0]
        assert "fewer sold" in finding.summary

    def test_a_response_that_loses_money_is_flagged(self):
        # Steeper than -1: volume falls faster than price rises, so takings fall.
        finding = price_response(self._with_price_moves(-2.0))[0]
        assert finding.facts["revenue_follows_price"] is False
        assert finding.severity is Severity.WATCH

    def test_a_response_that_still_pays_is_not_alarming(self):
        # Shallower than -1: fewer units but more money.
        findings = price_response(self._with_price_moves(-0.5))
        assert findings
        assert findings[0].facts["revenue_follows_price"] is True
        assert findings[0].severity is Severity.NEUTRAL

    def test_a_flat_price_is_not_tested(self):
        """The reason this found nothing on the real sample files.

        Prices there varied by under 3%, which is noise. A slope fitted to noise
        is a number with no meaning, so the product is skipped rather than
        reported with a confident figure attached.
        """
        assert price_response(frame_from(sales())) == []

    def test_cause_is_never_claimed(self):
        finding = price_response(self._with_price_moves(-2.0))[0]
        joined = " ".join(finding.evidence.notes).lower()
        assert "not cause" in joined or "association" in joined
        # And the summary must not promise what happens if the price is changed.
        assert "will" not in finding.summary.lower()

    def test_the_family_is_corrected(self):
        finding = price_response(self._with_price_moves(-2.0))[0]
        assert finding.evidence.correction == "Benjamini-Hochberg FDR"
        assert finding.evidence.adjusted_p is not None

    def test_only_a_couple_are_reported(self):
        # One is a finding; a dozen is a table.
        rng = np.random.default_rng(3)
        rows = []
        order = 0
        for i, month in enumerate(pd.date_range("2025-01-01", periods=14, freq="MS")):
            for p in range(6):
                price = 1000.0 * (1 + 0.3 * np.sin(i + p))
                units = 100.0 * (price / 1000.0) ** -2.0
                for _ in range(4):
                    order += 1
                    rows.append(
                        {
                            "Order Date": month
                            + pd.Timedelta(days=int(rng.integers(0, 27))),
                            "Order ID": f"ORD-{order}",
                            "Product": f"P{p}",
                            "Quantity": max(1, int(units / 4)),
                            "Unit Price": round(price, 2),
                        }
                    )
        assert len(price_response(frame_from(rows))) <= 2


class TestLifecycle:
    """Products arriving and products going quiet."""

    def _stopping(self):
        rows = sales(months=14, products={"Steady": (5000.0, 40)})
        # A product that traded for eight months and then vanished.
        rng = np.random.default_rng(7)
        order = 90_000
        for i, month in enumerate(pd.date_range("2025-01-01", periods=8, freq="MS")):
            for _ in range(4):
                order += 1
                rows.append(
                    {
                        "Order Date": month + pd.Timedelta(days=int(rng.integers(0, 27))),
                        "Order ID": f"OLD-{order}",
                        "Product": "Discontinued",
                        "Quantity": 10,
                        "Unit Price": 8000.0,
                    }
                )
        return frame_from(rows)

    def test_a_product_that_stopped_is_found(self):
        """The case no ranking can show, because a stopped product has no rows."""
        findings = lifecycle(self._stopping())
        stopped = [f for f in findings if f.id == "products_stopped"]
        assert stopped, "a product that stopped selling went unnoticed"
        assert "Discontinued" in stopped[0].summary

    def test_it_says_what_the_product_was_worth(self):
        stopped = [f for f in lifecycle(self._stopping()) if f.id == "products_stopped"][0]
        assert stopped.facts["stopped"][0]["was_worth"] > 0
        assert stopped.facts["stopped"][0]["last_seen"]

    def test_stopping_is_worth_attention(self):
        stopped = [f for f in lifecycle(self._stopping()) if f.id == "products_stopped"][0]
        assert stopped.severity is Severity.WATCH

    def test_the_cause_is_not_guessed(self):
        stopped = [f for f in lifecycle(self._stopping()) if f.id == "products_stopped"][0]
        joined = " ".join(stopped.evidence.notes).lower()
        assert "cannot tell" in joined or "stockout" in joined

    def test_a_new_product_is_noticed(self):
        rows = sales(months=14, products={"Steady": (5000.0, 40)})
        rng = np.random.default_rng(9)
        order = 70_000
        for month in pd.date_range("2026-01-01", periods=DORMANT_PERIODS, freq="MS"):
            for _ in range(4):
                order += 1
                rows.append(
                    {
                        "Order Date": month + pd.Timedelta(days=int(rng.integers(0, 27))),
                        "Order ID": f"NEW-{order}",
                        "Product": "Brand New",
                        "Quantity": 12,
                        "Unit Price": 9000.0,
                    }
                )
        arrived = [f for f in lifecycle(frame_from(rows)) if f.id == "products_arrived"]
        assert arrived
        assert "Brand New" in arrived[0].summary

    def test_a_one_off_appearance_is_not_a_loss(self):
        """Something sold twice and never again was never established."""
        rows = sales(months=14, products={"Steady": (5000.0, 40)})
        rows.append(
            {
                "Order Date": pd.Timestamp("2025-02-05"),
                "Order ID": "ONE-1",
                "Product": "Tried Once",
                "Quantity": 1,
                "Unit Price": 100.0,
            }
        )
        stopped = [f for f in lifecycle(frame_from(rows)) if f.id == "products_stopped"]
        assert not stopped or "Tried Once" not in stopped[0].summary

    def test_a_steady_file_reports_nothing(self):
        assert lifecycle(frame_from(sales())) == []


class TestOrderSpread:
    """The shape an average hides."""

    def _skewed(self):
        rng = np.random.default_rng(13)
        rows = []
        for i in range(200):
            # Mostly small orders, a few very large ones.
            big = i % 20 == 0
            rows.append(
                {
                    "Order Date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i % 90),
                    "Order ID": f"ORD-{i}",
                    "Product": "Rice" if i % 2 else "Beans",
                    "Quantity": 200 if big else int(rng.integers(1, 4)),
                    "Unit Price": 1000.0,
                }
            )
        return frame_from(rows)

    def test_a_skewed_mix_is_reported(self):
        findings = order_spread(self._skewed())
        assert findings
        assert findings[0].type is FindingType.DISTRIBUTION

    def test_it_gives_the_median_and_the_average(self):
        facts = order_spread(self._skewed())[0].facts
        assert facts["median"] < facts["mean"]
        assert facts["top_decile_share"] > 0

    def test_it_names_the_gap_in_words(self):
        summary = order_spread(self._skewed())[0].summary
        assert "Half your" in summary and "average is" in summary

    def test_an_even_spread_says_nothing(self):
        """No card unless the average is actually misleading."""
        rows = [
            {
                "Order Date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i % 90),
                "Order ID": f"ORD-{i}",
                "Product": "Rice" if i % 2 else "Beans",
                "Quantity": 10,
                "Unit Price": 1000.0,
            }
            for i in range(200)
        ]
        assert order_spread(frame_from(rows)) == []

    def test_too_little_data_says_nothing(self):
        rows = [
            {
                "Order Date": pd.Timestamp("2025-01-01"),
                "Order ID": f"ORD-{i}",
                "Product": "Rice" if i % 2 else "Beans",
                "Quantity": i + 1,
                "Unit Price": 1000.0,
            }
            for i in range(5)
        ]
        assert order_spread(frame_from(rows)) == []


class TestNoneOfThemInventAnything:
    """Every one of these must stay silent on a file with no pattern in it."""

    @pytest.mark.parametrize(
        "analysis", [concentration_classes, price_response, lifecycle, order_spread]
    )
    def test_a_flat_business_produces_nothing_alarming(self, analysis):
        findings = analysis(frame_from(sales(products={f"P{i}": (5000.0, 30) for i in range(6)})))
        assert all(f.severity is not Severity.URGENT for f in findings)
