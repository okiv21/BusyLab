"""Segmentation and cross-product relationships.

This is where the engine is most likely to lie, so it is where the discipline
has to be tightest.

Automatically slicing products by channel by region by month runs to hundreds
of comparisons, and at a 5 percent threshold about one in twenty comes back
"significant" by pure chance. Spec 3.3 calls that out as the sharpest failure
mode in the product: without a correction, BusyLab generates impressive-looking
garbage. Every family of tests raised here is therefore corrected together with
Benjamini-Hochberg before a single finding is emitted, and anything that only
survives uncorrected is framed as "worth a look" rather than claimed.

Correlation gets the same treatment plus an explicit caveat, because products
selling together does not mean one causes the other, and a chart implying it
does would be worse than no chart.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..findings import Evidence, Finding, FindingType, Severity
from ..roles import Tier
from . import stats
from .dataset import PRODUCT, REVENUE, SalesFrame

#: A dimension with more levels than this is an identifier, not a segment.
MAX_LEVELS = 12
#: Each level needs at least this many rows before it is worth testing.
MIN_ROWS_PER_LEVEL = 5
#: Correlations below this are not worth a sentence even when significant.
MIN_CORRELATION = 0.5


def segmentation(frame: SalesFrame) -> list[Finding]:
    """Do results genuinely differ across the file's categorical dimensions?

    Every dimension is tested, then the whole family is corrected at once.
    Testing five dimensions and reporting the best one without correction is
    the same error as testing five hypotheses and keeping the winner.
    """
    if not frame.dimensions:
        return []

    candidates: list[tuple[str, str, stats.TestResult, pd.Series]] = []
    for column, label in frame.dimensions.items():
        if column not in frame.data.columns:
            continue
        groups = frame.data[column]
        levels = groups.dropna().nunique()
        if levels < 2 or levels > MAX_LEVELS:
            continue
        counts = groups.value_counts()
        if counts.min() < MIN_ROWS_PER_LEVEL:
            usable = counts[counts >= MIN_ROWS_PER_LEVEL].index
            if len(usable) < 2:
                continue
            mask = groups.isin(usable)
        else:
            mask = pd.Series(True, index=frame.data.index)

        values = frame.data.loc[mask, REVENUE]
        result = stats.group_difference_test(values, groups[mask])
        if result is None:
            continue
        means = frame.data.loc[mask].groupby(groups[mask])[REVENUE].mean()
        candidates.append((column, label, result, means))

    if not candidates:
        return []

    corrected = stats.correct_family([c[2] for c in candidates])

    findings: list[Finding] = []
    for (column, label, _, means), result in zip(candidates, corrected):
        if not (result.significant or result.worth_a_look):
            continue

        ranked = means.sort_values(ascending=False)
        top, bottom = str(ranked.index[0]), str(ranked.index[-1])
        gap = stats.safe_pct_change(float(ranked.iloc[0]), float(ranked.iloc[-1]))
        if gap is None:
            continue

        confirmed = result.significant
        if confirmed:
            summary = (
                f"Average order value differs by {label}: {top} runs about "
                f"{abs(gap) * 100:.0f}% higher than {bottom}, and that gap holds "
                "up after accounting for how many comparisons were made."
            )
            severity, importance = Severity.WATCH, 0.7
        else:
            summary = (
                f"{top} may run higher than {bottom} by {label}, but once the "
                "number of comparisons is accounted for the difference is not "
                "yet clear. Worth a look rather than a conclusion."
            )
            severity, importance = Severity.NEUTRAL, 0.4

        findings.append(
            Finding(
                id=f"segmentation_{column}",
                type=FindingType.SEGMENTATION,
                summary=summary,
                facts={
                    "dimension": label,
                    "column": column,
                    "levels": int(len(ranked)),
                    "highest": top,
                    "highest_value": float(ranked.iloc[0]),
                    "lowest": bottom,
                    "lowest_value": float(ranked.iloc[-1]),
                    "gap_pct": gap,
                    "confirmed": confirmed,
                    "means": {str(k): float(v) for k, v in ranked.items()},
                },
                evidence=Evidence(
                    method=result.method,
                    p_value=result.p_value,
                    adjusted_p=result.adjusted_p,
                    sample_size=result.sample_size,
                    correction="Benjamini-Hochberg FDR",
                    notes=[
                        f"{len(candidates)} dimensions tested together; "
                        "p-values corrected across the whole family."
                    ],
                ),
                severity=severity,
                importance=importance,
                tier=Tier.SEGMENT,
                chart_data={
                    "groups": [
                        {"label": str(k), "value": float(v)} for k, v in ranked.items()
                    ]
                },
            )
        )

    return findings


def product_relationships(frame: SalesFrame) -> list[Finding]:
    """Which products move together over time.

    Correlation only. The caveat is carried on the finding itself rather than
    left to the reader, because "these two move together" is genuinely useful
    and "one causes the other" is not something this data can support.
    """
    freq = frame.natural_frequency()
    matrix = frame.product_period(freq=freq, value=REVENUE)
    if matrix.shape[0] < stats.MIN_POINTS or matrix.shape[1] < 2:
        return []

    # Drop products that barely trade; their correlation is noise.
    active = matrix.loc[:, (matrix > 0).sum() >= max(3, len(matrix) // 3)]
    if active.shape[1] < 2:
        return []

    from scipy import stats as scipy_stats

    pairs: list[tuple[str, str, float, float]] = []
    columns = list(active.columns)
    for i, a in enumerate(columns):
        for b in columns[i + 1 :]:
            x, y = active[a].to_numpy(float), active[b].to_numpy(float)
            if np.std(x) == 0 or np.std(y) == 0:
                continue
            r, p = scipy_stats.pearsonr(x, y)
            if np.isfinite(r) and np.isfinite(p):
                pairs.append((str(a), str(b), float(r), float(p)))

    if not pairs:
        return []

    # One family: every pair tested at once.
    _, adjusted = stats.benjamini_hochberg([p for *_, p in pairs])

    scored = [
        {
            "a": a,
            "b": b,
            "correlation": r,
            "p_value": p,
            "adjusted_p": adj,
        }
        for (a, b, r, p), adj in zip(pairs, adjusted)
    ]
    strong = [
        s
        for s in scored
        if s["adjusted_p"] < stats.ALPHA and abs(s["correlation"]) >= MIN_CORRELATION
    ]
    if not strong:
        return []

    strong.sort(key=lambda s: -abs(s["correlation"]))
    best = strong[0]
    together = "together" if best["correlation"] > 0 else "in opposite directions"

    return [
        Finding(
            id="product_relationships",
            type=FindingType.RELATIONSHIP,
            summary=(
                f"{best['a']} and {best['b']} move {together} from period to "
                f"period (correlation {best['correlation']:.2f}). This shows "
                "they rise and fall alongside each other, not that one drives "
                "the other."
            ),
            facts={
                "pairs": strong[:10],
                "strongest": best,
                "pairs_tested": len(pairs),
                "item_count": int(active.shape[1]),
            },
            evidence=Evidence(
                method="Pearson correlation across periods",
                p_value=best["p_value"],
                adjusted_p=best["adjusted_p"],
                sample_size=int(active.shape[0]),
                correction="Benjamini-Hochberg FDR",
                notes=[
                    f"{len(pairs)} product pairs tested together.",
                    "Correlation is not causation; both may follow a third cause.",
                ],
            ),
            severity=Severity.NEUTRAL,
            importance=0.55,
            chart_data={
                "matrix": [
                    {"a": s["a"], "b": s["b"], "correlation": s["correlation"]}
                    for s in scored
                ],
                "products": [str(c) for c in active.columns],
            },
        )
    ]
