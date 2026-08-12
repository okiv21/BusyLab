"""The statistics. All of it deterministic, none of it decided by a model.

Two things here carry most of the product's honesty.

**Trend versus noise.** A 15 percent drop is either a real change or ordinary
variance, and telling those apart is the judgement a human cannot make by eye
(spec 5). Every claim that something moved goes through a test.

**Multiple comparisons.** Slicing products by salespeople by regions by months
runs to hundreds of tests, and at a 5 percent threshold roughly one in twenty
will look significant by pure chance. Without a correction the engine produces
impressive-looking garbage; spec 3.3 names this the sharpest failure mode in
the product. Benjamini-Hochberg is used rather than Bonferroni because
Bonferroni is so conservative on a family of hundreds that it would suppress
the real findings along with the false ones.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

#: Standard significance threshold, applied to corrected p-values.
ALPHA = 0.05
#: Between ALPHA and this, a result is framed as "worth a look" rather than
#: asserted as a finding (spec 3.3 explicitly allows this softer framing).
WORTH_A_LOOK = 0.15
#: Below this many observations, no test is trustworthy and none is claimed.
MIN_POINTS = 6


@dataclass(frozen=True)
class TestResult:
    """One statistical test, with everything needed to report it honestly."""

    method: str
    statistic: float
    p_value: float
    sample_size: int
    effect: float = 0.0
    ci_low: float | None = None
    ci_high: float | None = None
    adjusted_p: float | None = None

    @property
    def significant(self) -> bool:
        p = self.adjusted_p if self.adjusted_p is not None else self.p_value
        return p < ALPHA

    @property
    def worth_a_look(self) -> bool:
        p = self.adjusted_p if self.adjusted_p is not None else self.p_value
        return ALPHA <= p < WORTH_A_LOOK

    def with_adjusted(self, adjusted: float) -> "TestResult":
        return TestResult(
            self.method,
            self.statistic,
            self.p_value,
            self.sample_size,
            self.effect,
            self.ci_low,
            self.ci_high,
            adjusted,
        )


def benjamini_hochberg(
    p_values: list[float], alpha: float = ALPHA
) -> tuple[list[bool], list[float]]:
    """Control the false discovery rate across a family of tests.

    Returns ``(rejected, adjusted)``. ``adjusted`` values are monotonic and
    capped at 1.0, so they can be reported directly as "the p-value once we
    account for how many things we looked at".

    This is what stops automatic segmentation inventing findings. Testing 200
    slices at p < 0.05 yields about 10 false positives by construction; after
    this correction, roughly 5 percent of what survives is expected to be
    spurious instead.
    """
    n = len(p_values)
    if n == 0:
        return [], []
    if n == 1:
        p = min(max(p_values[0], 0.0), 1.0)
        return [p < alpha], [p]

    order = np.argsort(p_values)
    ordered = np.asarray(p_values, dtype=float)[order]
    ranks = np.arange(1, n + 1)

    # Step-up: adjusted p at rank i is the running minimum from the largest
    # rank downwards, which keeps the sequence monotonic.
    scaled = ordered * n / ranks
    adjusted_sorted = np.minimum.accumulate(scaled[::-1])[::-1]
    adjusted_sorted = np.clip(adjusted_sorted, 0.0, 1.0)

    adjusted = np.empty(n, dtype=float)
    adjusted[order] = adjusted_sorted
    rejected = adjusted < alpha
    return rejected.tolist(), adjusted.tolist()


def correct_family(results: list[TestResult], alpha: float = ALPHA) -> list[TestResult]:
    """Apply the FDR correction across a whole family of tests at once."""
    if not results:
        return []
    _, adjusted = benjamini_hochberg([r.p_value for r in results], alpha)
    return [r.with_adjusted(a) for r, a in zip(results, adjusted)]


def slope_test(x, y) -> TestResult | None:
    """Least squares slope of ``y`` against ``x``, with a p-value.

    ``trend_test`` is this against time; this is the general case, for asking how
    one measured quantity moves with another. Kept separate rather than
    generalising trend_test, because that function reports its slope per period
    and in percentage-of-level terms, which are meaningful for time and
    meaningless for anything else.

    Both series are expected to be prepared by the caller - logged, cleaned,
    aligned. This does the arithmetic and reports how sure it is, nothing more.
    """
    xs = np.asarray(x, dtype=float)
    ys = np.asarray(y, dtype=float)
    if xs.size != ys.size or xs.size < MIN_POINTS:
        return None
    ok = np.isfinite(xs) & np.isfinite(ys)
    xs, ys = xs[ok], ys[ok]
    if xs.size < MIN_POINTS:
        return None
    # A flat input has no slope to find, and linregress divides by zero on it.
    if np.allclose(xs, xs[0]) or np.allclose(ys, ys[0]):
        return None

    fit = scipy_stats.linregress(xs, ys)
    if not np.isfinite(fit.pvalue):
        return None

    df = xs.size - 2
    ci_low = ci_high = None
    if df > 0 and fit.stderr is not None and np.isfinite(fit.stderr):
        margin = float(scipy_stats.t.ppf(0.975, df) * fit.stderr)
        ci_low, ci_high = float(fit.slope - margin), float(fit.slope + margin)

    return TestResult(
        method="least squares slope",
        statistic=float(fit.slope),
        p_value=float(fit.pvalue),
        sample_size=int(xs.size),
        effect=float(fit.rvalue**2),
        ci_low=ci_low,
        ci_high=ci_high,
    )


def trend_test(series: pd.Series) -> TestResult | None:
    """Is this series genuinely rising or falling, or just wobbling?

    Ordinary least squares against time. The slope is reported per period and
    as a percentage of the mean level, which is what a business actually cares
    about, and the p-value answers "could a flat business have produced this
    by chance".
    """
    clean = pd.Series(series).dropna()
    if len(clean) < MIN_POINTS:
        return None
    y = clean.to_numpy(dtype=float)
    x = np.arange(len(y), dtype=float)
    if np.allclose(y, y[0]):
        return None

    fit = scipy_stats.linregress(x, y)
    level = float(np.mean(np.abs(y)))
    effect = float(fit.slope / level) if level else 0.0

    # 95% interval on the slope, expressed in the same per-period units.
    df = len(y) - 2
    ci_low = ci_high = None
    if df > 0 and fit.stderr is not None and np.isfinite(fit.stderr):
        margin = float(scipy_stats.t.ppf(0.975, df) * fit.stderr)
        ci_low, ci_high = float(fit.slope - margin), float(fit.slope + margin)

    return TestResult(
        method="least squares trend",
        statistic=float(fit.slope),
        p_value=float(fit.pvalue),
        sample_size=len(y),
        effect=effect,
        ci_low=ci_low,
        ci_high=ci_high,
    )


def level_shift_test(before: pd.Series, after: pd.Series) -> TestResult | None:
    """Did the average level actually change between two windows?

    Welch's t-test, which does not assume the two windows have equal variance.
    Business periods rarely do.
    """
    a = pd.Series(before).dropna().to_numpy(dtype=float)
    b = pd.Series(after).dropna().to_numpy(dtype=float)
    if len(a) < 3 or len(b) < 3:
        return None
    if np.allclose(np.concatenate([a, b]), a[0]):
        return None

    result = scipy_stats.ttest_ind(a, b, equal_var=False)
    mean_a, mean_b = float(np.mean(a)), float(np.mean(b))
    effect = (mean_b - mean_a) / abs(mean_a) if mean_a else 0.0
    return TestResult(
        method="Welch t-test",
        statistic=float(result.statistic),
        p_value=float(result.pvalue),
        sample_size=len(a) + len(b),
        effect=float(effect),
    )


def group_difference_test(
    values: pd.Series, groups: pd.Series
) -> TestResult | None:
    """Do these groups genuinely differ, or is it sampling noise?

    One-way ANOVA across three or more groups, Welch's t-test for two. This is
    the test behind "Sales rep B's average order is 30 percent higher", and it
    is exactly the one that must be FDR-corrected before anything is claimed.
    """
    frame = pd.DataFrame({"value": values, "group": groups}).dropna()
    if frame.empty:
        return None
    buckets = [
        g["value"].to_numpy(dtype=float)
        for _, g in frame.groupby("group")
        if len(g) >= 3
    ]
    if len(buckets) < 2:
        return None
    if all(np.allclose(b, buckets[0][0]) for b in buckets):
        return None

    if len(buckets) == 2:
        result = scipy_stats.ttest_ind(buckets[0], buckets[1], equal_var=False)
        method = "Welch t-test"
    else:
        result = scipy_stats.f_oneway(*buckets)
        method = "one-way ANOVA"

    means = [float(np.mean(b)) for b in buckets]
    overall = float(np.mean(np.concatenate(buckets)))
    effect = (max(means) - min(means)) / abs(overall) if overall else 0.0

    p = float(result.pvalue)
    if not np.isfinite(p):
        return None
    return TestResult(
        method=method,
        statistic=float(result.statistic),
        p_value=p,
        sample_size=int(sum(len(b) for b in buckets)),
        effect=float(effect),
    )


def deseasonalize(series: pd.Series, period: int | None = None) -> pd.Series | None:
    """Strip the repeating annual shape so a normal December dip is not news.

    Returns the series with the seasonal component removed, or None when there
    is not enough history to estimate one. Two full cycles is the minimum;
    below that, any "seasonality" found is the trend in disguise.
    """
    clean = pd.Series(series).dropna()
    if period is None:
        period = _infer_period(clean)
    if period is None or len(clean) < 2 * period:
        return None

    try:
        from statsmodels.tsa.seasonal import STL

        result = STL(clean, period=period, robust=True).fit()
        return clean - result.seasonal
    except Exception:
        return None


def seasonal_strength(series: pd.Series, period: int | None = None) -> float | None:
    """How much of the variation is the calendar rather than the business.

    0 means no repeating pattern, 1 means the series is almost entirely
    seasonal. Used to decide whether a dip deserves a finding at all.
    """
    clean = pd.Series(series).dropna()
    if period is None:
        period = _infer_period(clean)
    if period is None or len(clean) < 2 * period:
        return None
    try:
        from statsmodels.tsa.seasonal import STL

        result = STL(clean, period=period, robust=True).fit()
        residual_var = float(np.var(result.resid))
        seasonal_var = float(np.var(result.seasonal + result.resid))
        if seasonal_var <= 0:
            return 0.0
        return float(max(0.0, min(1.0, 1.0 - residual_var / seasonal_var)))
    except Exception:
        return None


def _infer_period(series: pd.Series) -> int | None:
    """Guess the seasonal cycle length from the index frequency."""
    if not isinstance(series.index, pd.DatetimeIndex) or len(series) < 4:
        return None
    freq = pd.infer_freq(series.index)
    if freq is None:
        gap = series.index.to_series().diff().median()
        if pd.isna(gap):
            return None
        days = gap.days
        if days >= 28:
            return 12
        if days >= 7:
            return 52
        return 7
    head = freq[0].upper()
    if head == "M":
        return 12
    if head == "W":
        return 52
    if head == "Q":
        return 4
    if head == "D":
        return 7
    return None


def concentration(values: pd.Series) -> dict[str, float]:
    """How much of the total sits in how few items.

    Reports the top-1 and top-2 shares and the Herfindahl index. A business
    where 68 percent of profit comes from one product is a different business
    from one where it is spread evenly, and that is a fact about risk rather
    than a ranking.
    """
    clean = pd.Series(values).dropna()
    clean = clean[clean > 0].sort_values(ascending=False)
    total = float(clean.sum())
    if total <= 0 or clean.empty:
        return {}

    shares = (clean / total).to_numpy(dtype=float)
    cumulative = np.cumsum(shares)
    return {
        "total": total,
        "items": int(len(clean)),
        "top1_share": float(shares[0]),
        "top2_share": float(cumulative[1]) if len(shares) > 1 else float(shares[0]),
        "top3_share": float(cumulative[2]) if len(shares) > 2 else float(cumulative[-1]),
        "herfindahl": float(np.sum(shares**2)),
        "items_for_half": int(np.searchsorted(cumulative, 0.5) + 1),
    }


def safe_pct_change(new: float, old: float) -> float | None:
    """Percentage change that refuses to divide by zero."""
    if old is None or new is None or old == 0 or not np.isfinite(old):
        return None
    return float((new - old) / abs(old))
