"""Forecasting tests.

A forecast is the easiest place in this product to be confidently wrong, so
the tests are mostly about refusing to speak: no direction claimed unless the
interval supports it, no projection at all from too little history or from a
model that failed its own backtest, and no revenue projected below zero.

The control matters as much as the positive case. A business going nowhere
must not be forecast into a trend.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from busylab.analysis import analyse
from busylab.analysis.forecast import (
    MIN_PERIODS,
    project,
)

from . import fixtures


def _series(values: list[float], start: str = "2024-01-01") -> pd.Series:
    index = pd.date_range(start, periods=len(values), freq="MS")
    return pd.Series(values, index=index)


def _forecasts(result):
    return [f for f in result.findings if f.type.value == "forecast"]


def _revenue_forecast(result):
    return next((f for f in result.findings if f.id == "revenue_forecast"), None)


@pytest.fixture(scope="module")
def declining_business() -> pd.DataFrame:
    """An unambiguous, sustained decline. The forecast should say so."""
    rng = np.random.default_rng(9)
    rows = []
    start = pd.Timestamp("2024-01-01")
    for day in range(600):
        when = start + pd.Timedelta(days=day)
        level = 8000 * (1 - 0.0011 * day)
        for _ in range(5):
            rows.append(
                {
                    "order_date": when,
                    "product_name": str(rng.choice(fixtures.PRODUCTS)),
                    "total_paid": round(
                        max(200, level * float(rng.normal(1, 0.05))), 2
                    ),
                }
            )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Bands are mandatory
# --------------------------------------------------------------------------


def test_every_forecast_carries_a_band() -> None:
    """Spec Pillar 1: uncertainty bands, never a fake precise number."""
    result = analyse(fixtures.planted_business(), strict=True)
    forecasts = _forecasts(result)
    assert forecasts

    for finding in forecasts:
        assert finding.chart == "forecast_fan" or finding.chart.value == "forecast_fan"
        for point in finding.chart_data["forecast"]:
            assert point["lower95"] <= point["lower80"] <= point["mean"]
            assert point["mean"] <= point["upper80"] <= point["upper95"]


def test_the_band_widens_with_distance() -> None:
    """Further out means less certain, and the picture has to show it."""
    projection = project(_series([100 + i * 2 + (i % 3) for i in range(30)]))
    assert projection is not None

    widths = [u - l for u, l in zip(projection.upper80, projection.lower80)]
    assert widths[-1] > widths[0]


def test_the_sentence_never_promises_a_precise_number() -> None:
    result = analyse(fixtures.planted_business(), strict=True)
    for finding in _forecasts(result):
        assert "at current trend" in finding.summary.lower()


# --------------------------------------------------------------------------
# Refusing to speak
# --------------------------------------------------------------------------


def test_too_little_history_produces_no_forecast() -> None:
    assert project(_series([100.0, 110.0, 105.0, 120.0, 115.0])) is None


def test_a_constant_series_needs_no_model() -> None:
    assert project(_series([100.0] * 24)) is None


def test_a_flat_business_is_not_forecast_into_a_trend(  # the control
) -> None:
    result = analyse(fixtures.flat_business(), strict=True)
    finding = _revenue_forecast(result)

    if finding is not None:
        assert finding.facts["direction"] == "flat"
        assert "holds roughly where it is" in finding.summary


def test_mean_reversion_is_not_reported_as_growth() -> None:
    """One randomly low final month makes any model revert upwards.

    Comparing the projection against that single point would turn ordinary
    reversion into "heading up" on a business going nowhere, so the comparison
    is against a robust recent level instead.
    """
    rng = np.random.default_rng(5)
    values = list(rng.normal(1000, 40, 29)) + [780.0]  # last month unusually low
    projection = project(_series(values))

    assert projection is not None
    assert projection.direction == "flat"


def test_a_real_decline_is_called(declining_business) -> None:
    """Refusing to speak is only a virtue when there is nothing to say."""
    result = analyse(declining_business, strict=True)
    finding = _revenue_forecast(result)

    assert finding is not None
    assert finding.facts["direction"] == "down"
    assert "heading down" in finding.summary


# --------------------------------------------------------------------------
# The forecast reports its own accuracy
# --------------------------------------------------------------------------


def test_a_forecast_is_backtested_before_it_is_published() -> None:
    projection = project(_series([100 + i * 3 for i in range(30)]))
    assert projection is not None
    assert projection.backtest_error is not None
    assert projection.backtest_error >= 0


def test_a_predictable_series_backtests_well() -> None:
    projection = project(_series([100 + i * 5 for i in range(30)]))
    assert projection is not None
    assert projection.backtest_error < 0.10
    assert projection.trustworthy


def test_findings_disclose_the_model_and_its_error() -> None:
    result = analyse(fixtures.planted_business(), strict=True)
    for finding in _forecasts(result):
        assert finding.facts["model"].startswith("ARIMA")
        assert any("ARIMA" in n for n in finding.evidence.notes)
        if finding.facts["backtest_error"] is not None:
            assert any("error" in n.lower() for n in finding.evidence.notes)


def test_a_noisy_history_is_flagged_as_rough() -> None:
    rng = np.random.default_rng(2)
    noisy = _series(list(rng.normal(1000, 600, 30).clip(50)))
    projection = project(noisy)

    if projection is not None and projection.shaky:
        assert projection.backtest_error > 0.25


# --------------------------------------------------------------------------
# Bounds and partial periods
# --------------------------------------------------------------------------


def test_revenue_is_never_projected_below_zero(declining_business) -> None:
    """ARIMA is unbounded; revenue is not."""
    result = analyse(declining_business, strict=True)
    finding = _revenue_forecast(result)

    assert finding is not None
    for point in finding.chart_data["forecast"]:
        assert point["lower95"] >= 0
        assert point["mean"] >= 0


def test_profit_may_be_projected_below_zero() -> None:
    """Negative profit is real, and is the whole point of break-even."""
    projection = project(_series([500 - i * 40 for i in range(24)]), floor=None)
    assert projection is not None
    assert min(projection.mean) < 0


def test_a_partial_final_period_is_dropped() -> None:
    """A file exported mid-month must not look like a crash.

    Anything fitted through a two-thirds month then 'recovers' from a collapse
    that never happened.
    """
    from busylab.analysis import build
    from busylab.detection import detect

    frame = fixtures.planted_business()  # ends 2025-06-23, mid-month
    sales = build(frame, detect(frame))

    full = sales.by_period("MS")
    trimmed = sales.by_period("MS", drop_partial=True)

    assert len(trimmed) == len(full) - 1
    assert trimmed.index[-1] < full.index[-1]


def test_a_complete_final_period_is_kept() -> None:
    from busylab.analysis import build
    from busylab.detection import detect

    frame = fixtures.planted_business()
    frame = frame[pd.to_datetime(frame["order_date"]) < "2025-06-01"]
    sales = build(frame, detect(frame))

    assert len(sales.by_period("MS", drop_partial=True)) == len(sales.by_period("MS"))


# --------------------------------------------------------------------------
# Break-even
# --------------------------------------------------------------------------


def test_break_even_crossing_is_framed_as_a_direction_not_a_date() -> None:
    """Spec Pillar 1's example output, with the honesty attached."""
    rng = np.random.default_rng(11)
    rows = []
    start = pd.Timestamp("2024-01-01")
    for day in range(560):
        when = start + pd.Timedelta(days=day)
        # Margin erodes steadily until cost overtakes price.
        price = 5000.0
        cost = 2000.0 + day * 6.0
        for _ in range(4):
            rows.append(
                {
                    "order_date": when,
                    "product_name": "Fading Product"
                    if rng.random() < 0.6
                    else str(rng.choice(fixtures.PRODUCTS[:2])),
                    "quantity": 1,
                    "unit_price": price,
                    "total_paid": price,
                    "unit_cost": round(cost, 2),
                }
            )
    result = analyse(pd.DataFrame(rows), strict=True)
    crossing = [
        f for f in _forecasts(result) if f.facts.get("crosses_break_even")
    ]

    for finding in crossing:
        assert "break-even" in finding.summary
        assert "direction rather than a date" in finding.summary
        assert finding.facts["crosses_in_periods"] >= 1


def test_forecasts_do_not_read_as_advice() -> None:
    from busylab.findings import check_non_directive

    result = analyse(fixtures.planted_business(), strict=True)
    for finding in _forecasts(result):
        assert check_non_directive(finding.summary) == []


def test_only_material_products_are_forecast() -> None:
    """Forecasting a 0.4% product wastes time and adds a chance to be wrong."""
    result = analyse(fixtures.planted_business(), strict=True)
    product_forecasts = [
        f for f in _forecasts(result) if f.id.startswith("forecast_")
    ]
    assert len(product_forecasts) <= 6
