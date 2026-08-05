"""Pillar 1: where this is heading, and how sure we are.

**Model policy (spec Pillar 1): ARIMA first, and possibly only.** statsmodels
ARIMA is light, interpretable, runs comfortably on a CPU-only laptop and on a
small deployed instance, and for monthly SME sales data it is often good
enough. Deep models are parked until there is a clear accuracy gap and a
compute budget, because they blow past small-instance memory and destroy local
iteration speed.

Two rules make a forecast honest rather than decorative:

**Bands are mandatory.** A single projected number invites a business to plan
against a precision that does not exist. Every forecast here carries an 80%
and a 95% interval, and the sentence always says "at current trend".

**The forecast reports its own accuracy.** Before projecting forward, the model
is refit on all but the last few periods and scored against what actually
happened. If it could not predict the past it does not get to assert the
future: a forecast that fails its own backtest is either downgraded or
withheld. This is the check that stops a confident-looking fan being drawn
through data that has no predictable structure at all.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..findings import Evidence, Finding, FindingType, Severity
from ..roles import Tier
from . import stats
from .dataset import PRODUCT, PROFIT, REVENUE, SalesFrame

#: Below this many periods an ARIMA fit is arithmetic, not evidence.
MIN_PERIODS = 12
#: How far forward to project. Beyond a quarter the bands are so wide the
#: picture stops being useful.
DEFAULT_HORIZON = 3
#: Periods held back to score the model against reality.
BACKTEST_PERIODS = 3
#: Above this backtest error the model has not earned the right to project.
MAX_BACKTEST_ERROR = 0.45
#: Below this the forecast is reported but framed as rough.
SHAKY_BACKTEST_ERROR = 0.25
#: Only forecast products that actually matter to the business.
MATERIAL_SHARE = 0.05
#: Cap on how many per-product models to fit, for the sake of the request.
MAX_PRODUCTS = 6

#: Bounded grid. Wide enough for monthly SME data, small enough to stay fast.
_ORDERS: tuple[tuple[int, int, int], ...] = (
    (0, 1, 0),  # random walk, the honest baseline
    (1, 1, 0),
    (0, 1, 1),
    (1, 1, 1),
    (2, 1, 0),
    (0, 1, 2),
    (2, 1, 2),
    (1, 0, 0),
    (1, 0, 1),
    (2, 0, 0),
)


@dataclass
class Projection:
    """One fitted forecast, with everything needed to draw and caveat it."""

    periods: list[str]
    mean: list[float]
    lower80: list[float]
    upper80: list[float]
    lower95: list[float]
    upper95: list[float]
    order: tuple[int, int, int]
    aic: float
    #: Mean absolute percentage error on held-out periods, 0.12 being 12%.
    backtest_error: float | None
    history_periods: list[str] = field(default_factory=list)
    history: list[float] = field(default_factory=list)

    @property
    def model_name(self) -> str:
        p, d, q = self.order
        return f"ARIMA({p},{d},{q})"

    @property
    def trustworthy(self) -> bool:
        return (
            self.backtest_error is not None
            and self.backtest_error <= MAX_BACKTEST_ERROR
        )

    @property
    def shaky(self) -> bool:
        return (
            self.backtest_error is not None
            and self.backtest_error > SHAKY_BACKTEST_ERROR
        )

    @property
    def reference_level(self) -> float:
        """Where the business is now, robust to one odd period.

        The median of the recent past rather than the single last point. A
        randomly low final month makes any model revert upwards, and comparing
        against that one point turns ordinary mean reversion into "heading
        up" on a business that is not going anywhere.
        """
        recent = self.history[-6:] if len(self.history) >= 6 else self.history
        return float(np.median(recent)) if recent else 0.0

    @property
    def direction(self) -> str:
        """Which way this is heading — but only when the band agrees.

        A direction is claimed only if the whole 80% interval sits above or
        below where the business currently is. If the interval still contains
        that level then up and down are both on the table, and saying "heading
        up" because the central line ticks upwards is exactly the fake
        precision bands exist to prevent (spec Pillar 1).
        """
        if not self.history or not self.mean:
            return "flat"
        level = self.reference_level
        # The direction must hold across the whole horizon, not merely at the
        # final point. An oscillating fit can land its last step on a peak,
        # and "heading up" should mean consistently up rather than up on the
        # month we happened to stop at.
        if min(self.lower80) > level:
            return "up"
        if max(self.upper80) < level:
            return "down"
        return "flat"

    @property
    def total_change(self) -> float | None:
        if not self.history or not self.mean:
            return None
        return stats.safe_pct_change(self.mean[-1], self.history[-1])

    def to_chart(self) -> dict:
        """Payload for the fan chart: history, then a widening band."""
        return {
            "history": [
                {"period": p, "value": float(v)}
                for p, v in zip(self.history_periods, self.history)
            ],
            "forecast": [
                {
                    "period": p,
                    "mean": float(m),
                    "lower80": float(l80),
                    "upper80": float(u80),
                    "lower95": float(l95),
                    "upper95": float(u95),
                }
                for p, m, l80, u80, l95, u95 in zip(
                    self.periods,
                    self.mean,
                    self.lower80,
                    self.upper80,
                    self.lower95,
                    self.upper95,
                )
            ],
            "model": self.model_name,
        }


def _fit(series: pd.Series, order: tuple[int, int, int]):
    """Fit one ARIMA, or return None if it will not converge."""
    from statsmodels.tsa.arima.model import ARIMA

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            model = ARIMA(series, order=order, enforce_stationarity=False,
                          enforce_invertibility=False)
            fitted = model.fit()
            if not np.isfinite(fitted.aic):
                return None
            return fitted
        except Exception:
            return None


def _best_order(series: pd.Series) -> tuple[tuple[int, int, int], float] | None:
    """Pick the order by AICc over a small grid.

    A full auto-ARIMA search would need pmdarima, which is another dependency
    for a marginal gain on twelve to thirty monthly points. Ten candidate
    orders is enough and stays fast.

    AICc rather than AIC because these series are short. On seventeen monthly
    points, plain AIC will happily choose a five-parameter model that has
    fitted the alternating noise as if it were a cycle, and then forecast that
    imaginary cycle forward with confident narrow bands. The small-sample
    correction is what stops that.
    """
    best: tuple[tuple[int, int, int], float] | None = None
    for order in _ORDERS:
        params = sum(order)
        # Leave enough degrees of freedom for the fit to mean anything.
        if len(series) <= params + 3:
            continue
        fitted = _fit(series, order)
        if fitted is None:
            continue
        score = getattr(fitted, "aicc", None)
        if score is None or not np.isfinite(score):
            score = fitted.aic
        if best is None or score < best[1]:
            best = (order, float(score))
    return best


def _backtest(series: pd.Series, order: tuple[int, int, int]) -> float | None:
    """Refit without the last few periods and score against what happened.

    Returns mean absolute percentage error, or None when there is not enough
    history to hold anything back.
    """
    if len(series) < MIN_PERIODS + BACKTEST_PERIODS:
        return None
    train = series.iloc[:-BACKTEST_PERIODS]
    actual = series.iloc[-BACKTEST_PERIODS:].to_numpy(dtype=float)

    fitted = _fit(train, order)
    if fitted is None:
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            predicted = np.asarray(
                fitted.forecast(steps=BACKTEST_PERIODS), dtype=float
            )
        except Exception:
            return None

    denominator = np.where(np.abs(actual) < 1e-9, np.nan, np.abs(actual))
    errors = np.abs(predicted - actual) / denominator
    errors = errors[np.isfinite(errors)]
    return float(np.mean(errors)) if len(errors) else None


def project(
    series: pd.Series,
    *,
    horizon: int = DEFAULT_HORIZON,
    floor: float | None = None,
) -> Projection | None:
    """Fit, validate and project a single series forward.

    Returns None rather than a guess whenever the history is too short or no
    model converges. Refusing to forecast is a legitimate answer.

    ``floor`` clamps the projection to a value the quantity cannot go below.
    ARIMA is unbounded, so a steep decline happily projects revenue through
    zero into negative numbers; revenue passes ``floor=0``. Profit passes
    nothing, because negative profit is real and is the whole point of the
    break-even check.
    """
    clean = pd.Series(series).dropna()
    if len(clean) < MIN_PERIODS:
        return None
    if float(clean.std()) == 0:
        return None  # a constant series needs no model

    chosen = _best_order(clean)
    if chosen is None:
        return None
    order, aic = chosen

    fitted = _fit(clean, order)
    if fitted is None:
        return None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            result = fitted.get_forecast(steps=horizon)
            mean = np.asarray(result.predicted_mean, dtype=float)
            ci80 = np.asarray(result.conf_int(alpha=0.20), dtype=float)
            ci95 = np.asarray(result.conf_int(alpha=0.05), dtype=float)
        except Exception:
            return None

    if not np.all(np.isfinite(mean)):
        return None

    if floor is not None:
        mean = np.maximum(mean, floor)
        ci80 = np.maximum(ci80, floor)
        ci95 = np.maximum(ci95, floor)

    # Future period labels continue the observed cadence.
    index = clean.index
    if isinstance(index, pd.DatetimeIndex) and len(index) >= 2:
        step = index[-1] - index[-2]
        future = [index[-1] + step * (i + 1) for i in range(horizon)]
        labels = [str(pd.Timestamp(d).date()) for d in future]
        history_labels = [str(pd.Timestamp(d).date()) for d in index]
    else:
        labels = [f"+{i + 1}" for i in range(horizon)]
        history_labels = [str(i) for i in index]

    return Projection(
        periods=labels,
        mean=mean.tolist(),
        lower80=ci80[:, 0].tolist(),
        upper80=ci80[:, 1].tolist(),
        lower95=ci95[:, 0].tolist(),
        upper95=ci95[:, 1].tolist(),
        order=order,
        aic=aic,
        backtest_error=_backtest(clean, order),
        history_periods=history_labels,
        history=clean.to_numpy(dtype=float).tolist(),
    )


def _crossing(values: list[float], threshold: float = 0.0) -> int | None:
    """Index of the first period a series drops below ``threshold``."""
    for i, value in enumerate(values):
        if value < threshold:
            return i
    return None


def _horizon_words(periods: int, freq: str) -> str:
    """A bare duration, e.g. "3 months". Callers add "about" where it reads."""
    unit = {"MS": "month", "W": "week", "D": "day"}.get(freq, "period")
    return f"{periods} {unit}" + ("" if periods == 1 else "s")


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


def revenue_forecast(frame: SalesFrame) -> list[Finding]:
    """Where total revenue is heading."""
    freq = frame.natural_frequency()
    series = frame.by_period(freq=freq, value=REVENUE, drop_partial=True)
    if len(series) < MIN_PERIODS:
        return []

    # Revenue cannot go below zero, however steep the decline.
    projection = project(series, floor=0.0)
    if projection is None or not projection.trustworthy:
        return []

    change = projection.total_change
    direction = projection.direction
    horizon = _horizon_words(len(projection.mean), freq)

    if direction == "flat":
        summary = (
            f"At current trend, revenue holds roughly where it is over the next "
            f"{horizon}, within a range of "
            f"{projection.lower80[-1]:,.0f} to {projection.upper80[-1]:,.0f}."
        )
        severity = Severity.NEUTRAL
    else:
        summary = (
            f"At current trend, revenue is heading {direction} over the next "
            f"{horizon}, landing somewhere between "
            f"{projection.lower80[-1]:,.0f} and {projection.upper80[-1]:,.0f}."
        )
        severity = Severity.WATCH if direction == "down" else Severity.GOOD

    notes = [
        f"{projection.model_name} chosen by AIC.",
        "Range shown is the 80% interval; the outer band is 95%.",
    ]
    if projection.backtest_error is not None:
        notes.append(
            f"Tested on the last {BACKTEST_PERIODS} periods it did not see: "
            f"average error {projection.backtest_error:.0%}."
        )
    if projection.shaky:
        notes.append("This history is hard to predict, so treat it as rough.")

    return [
        Finding(
            id="revenue_forecast",
            type=FindingType.FORECAST,
            summary=summary,
            facts={
                "direction": direction,
                "change_pct": change,
                "horizon_periods": len(projection.mean),
                "frequency": freq,
                "last_actual": projection.history[-1],
                "projected": projection.mean[-1],
                "low80": projection.lower80[-1],
                "high80": projection.upper80[-1],
                "low95": projection.lower95[-1],
                "high95": projection.upper95[-1],
                "model": projection.model_name,
                "backtest_error": projection.backtest_error,
            },
            evidence=Evidence(
                method=f"{projection.model_name} with backtest",
                sample_size=len(series),
                confidence_low=projection.lower80[-1],
                confidence_high=projection.upper80[-1],
                notes=notes,
            ),
            severity=severity,
            importance=0.68 if direction != "flat" else 0.4,
            chart_data=projection.to_chart(),
        )
    ]


def product_forecasts(frame: SalesFrame) -> list[Finding]:
    """Per-product projections, and any that are heading below break-even.

    Only material products are modelled. Forecasting a product that is 0.4% of
    revenue burns time and adds a chance to be alarmingly wrong about something
    that does not matter.
    """
    freq = frame.natural_frequency()
    revenue_by_product = frame.by_product(REVENUE)
    total = float(revenue_by_product.sum())
    if total <= 0:
        return []

    material = [
        str(p)
        for p, v in revenue_by_product.items()
        if v / total >= MATERIAL_SHARE
    ][:MAX_PRODUCTS]
    if not material:
        return []

    use_profit = frame.has_profit
    metric = PROFIT if use_profit else REVENUE
    matrix = frame.product_period(freq=freq, value=metric, drop_partial=True)
    if matrix.empty or len(matrix) < MIN_PERIODS:
        return []

    findings: list[Finding] = []
    for product in material:
        if product not in matrix.columns:
            continue
        series = matrix[product]
        # Profit is left unbounded: going below zero is the finding.
        projection = project(series, floor=None if use_profit else 0.0)
        if projection is None or not projection.trustworthy:
            continue

        crossing = _crossing(projection.mean) if use_profit else None
        # Only a story when the projection actually says something.
        if crossing is None and projection.direction == "flat":
            continue

        if crossing is not None:
            when = _horizon_words(crossing + 1, freq)
            summary = (
                f"At current trend, {product} is projected to fall below "
                f"break-even in about {when}. The range still allows for "
                f"{projection.lower80[crossing]:,.0f} to "
                f"{projection.upper80[crossing]:,.0f}, so this is a direction "
                "rather than a date."
            )
            severity = Severity.URGENT
            importance = 0.86
        else:
            label = "profit" if use_profit else "revenue"
            summary = (
                f"At current trend, {product}'s {label} is heading "
                f"{projection.direction} over the next "
                f"{_horizon_words(len(projection.mean), freq)}, between "
                f"{projection.lower80[-1]:,.0f} and "
                f"{projection.upper80[-1]:,.0f}."
            )
            severity = (
                Severity.WATCH if projection.direction == "down" else Severity.GOOD
            )
            importance = 0.6

        notes = [
            f"{projection.model_name} chosen by AIC.",
            "Range shown is the 80% interval.",
        ]
        if projection.backtest_error is not None:
            notes.append(
                f"Average error on {BACKTEST_PERIODS} held-out periods: "
                f"{projection.backtest_error:.0%}."
            )
        if projection.shaky:
            notes.append("This product's history is noisy, so treat it as rough.")

        findings.append(
            Finding(
                id=f"forecast_{product}",
                type=FindingType.FORECAST,
                summary=summary,
                facts={
                    "product": product,
                    "metric": "profit" if use_profit else "revenue",
                    "direction": projection.direction,
                    "change_pct": projection.total_change,
                    "crosses_break_even": crossing is not None,
                    "crosses_in_periods": (crossing + 1) if crossing is not None else None,
                    "horizon_periods": len(projection.mean),
                    "frequency": freq,
                    "last_actual": projection.history[-1],
                    "projected": projection.mean[-1],
                    "low80": projection.lower80[-1],
                    "high80": projection.upper80[-1],
                    "model": projection.model_name,
                    "backtest_error": projection.backtest_error,
                },
                evidence=Evidence(
                    method=f"{projection.model_name} with backtest",
                    sample_size=int(len(series.dropna())),
                    confidence_low=projection.lower80[-1],
                    confidence_high=projection.upper80[-1],
                    notes=notes,
                ),
                severity=severity,
                importance=importance,
                tier=Tier.MARGIN if use_profit else Tier.CORE,
                chart_data=projection.to_chart(),
            )
        )

    return findings
