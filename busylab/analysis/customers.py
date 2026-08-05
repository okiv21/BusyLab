"""Pillar 3: customer intelligence.

Everything here needs a customer id, and nothing here runs without one. The
four questions, from spec Pillar 3:

* **Repeat versus new over time.** Whether a decline is a loyalty problem or a
  discovery problem. Two businesses with identical revenue curves need
  completely different things depending on the answer.
* **RFM segmentation.** Champions, At risk, Lost, New — sorted automatically
  rather than by someone eyeballing a list.
* **Cohort retention.** Does each month's intake stick, or does the business
  refill a leaking bucket.
* **Basket analysis.** What gets bought together.

Basket analysis is the dangerous one. Every product pair is a test, so a
catalogue of thirty products is over four hundred comparisons, and lift on a
pair that co-occurred twice is meaningless however large the multiple looks.
It gets a minimum support floor *and* the same Benjamini-Hochberg correction as
segmentation, for exactly the reason spec 3.3 gives.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..findings import Evidence, Finding, FindingType, Severity
from ..roles import Tier
from . import stats
from .dataset import CUSTOMER, DATE, ORDER, PRODUCT, REVENUE, SalesFrame

#: A cohort smaller than this tells you nothing about retention.
MIN_COHORT_SIZE = 8
#: Fewer cohorts than this is not a retention curve.
MIN_COHORTS = 3
#: A pair must appear in at least this many orders before lift is meaningful.
MIN_PAIR_SUPPORT = 8
#: And in at least this share of orders, so a big file does not lower the bar.
MIN_PAIR_SUPPORT_SHARE = 0.01
#: Lift below this is not worth a sentence even when it is significant.
MIN_LIFT = 1.5
#: How stale a customer must be, in median-gap multiples, to count as lapsed.
LAPSED_GAP_MULTIPLE = 3.0


def _basket_key(frame: SalesFrame) -> str | None:
    """What counts as one basket.

    An order id is the real answer. Failing that, everything a customer bought
    on the same day is a reasonable stand-in, and the finding says which was
    used rather than quietly implying the stronger one.
    """
    if frame.has(ORDER):
        return ORDER
    if frame.has(CUSTOMER):
        return "_day_basket"
    return None


# --------------------------------------------------------------------------
# Repeat versus new
# --------------------------------------------------------------------------


def repeat_vs_new(frame: SalesFrame) -> list[Finding]:
    """Split revenue over time into first-time and returning customers.

    The composition shift is the finding. "Loyalty is fine, discovery is
    broken" and "we keep acquiring but nobody comes back" look identical in a
    revenue total and could not be more different to act on.
    """
    if not frame.has_customers:
        return []

    data = frame.data[[DATE, CUSTOMER, REVENUE]].dropna()
    if data.empty or data[CUSTOMER].nunique() < 10:
        return []

    freq = frame.natural_frequency()
    first_seen = data.groupby(CUSTOMER)[DATE].min()
    data = data.assign(
        is_new=data[CUSTOMER].map(first_seen).eq(data[DATE])
    )

    grouped = data.set_index(DATE).groupby(
        [pd.Grouper(freq=freq), "is_new"]
    )[REVENUE].sum().unstack(fill_value=0.0)
    if grouped.empty or len(grouped) < 4:
        return []

    new_col = grouped.get(True, pd.Series(0.0, index=grouped.index))
    repeat_col = grouped.get(False, pd.Series(0.0, index=grouped.index))

    # Drop a trailing partial period so the last point is not a false cliff.
    if len(grouped) > 1 and frame._last_period_incomplete(freq):
        new_col, repeat_col = new_col.iloc[:-1], repeat_col.iloc[:-1]
    if len(new_col) < 4:
        return []

    half = len(new_col) // 2
    new_before, new_after = float(new_col.iloc[:half].mean()), float(new_col.iloc[half:].mean())
    rep_before, rep_after = float(repeat_col.iloc[:half].mean()), float(repeat_col.iloc[half:].mean())

    new_change = stats.safe_pct_change(new_after, new_before)
    repeat_change = stats.safe_pct_change(rep_after, rep_before)
    if new_change is None or repeat_change is None:
        return []

    # Only interesting when the two halves of the business diverge.
    if abs(new_change - repeat_change) < 0.15:
        return []

    if new_change < repeat_change:
        summary = (
            f"Revenue from returning customers moved "
            f"{repeat_change * 100:+.0f}% while revenue from first-time buyers "
            f"moved {new_change * 100:+.0f}%. The change is in who is arriving, "
            "not in who is staying."
        )
        severity = Severity.WATCH if new_change < 0 else Severity.NEUTRAL
    else:
        summary = (
            f"Revenue from first-time buyers moved {new_change * 100:+.0f}% "
            f"while revenue from returning customers moved "
            f"{repeat_change * 100:+.0f}%. The change is in who is coming back, "
            "not in who is arriving."
        )
        severity = Severity.WATCH if repeat_change < 0 else Severity.NEUTRAL

    return [
        Finding(
            id="repeat_vs_new",
            type=FindingType.REPEAT_VS_NEW,
            summary=summary,
            facts={
                "new_change_pct": new_change,
                "repeat_change_pct": repeat_change,
                "new_before": new_before,
                "new_after": new_after,
                "repeat_before": rep_before,
                "repeat_after": rep_after,
                "driver": "acquisition" if new_change < repeat_change else "retention",
                "customers": int(data[CUSTOMER].nunique()),
            },
            evidence=Evidence(
                method="first-purchase split by period",
                sample_size=int(len(data)),
                notes=[
                    "A customer counts as new in the period of their first "
                    "recorded purchase, and returning after that."
                ],
            ),
            severity=severity,
            importance=0.74,
            tier=Tier.CUSTOMER,
            chart_data={
                "series": [
                    {
                        "period": str(pd.Timestamp(i).date()),
                        "repeat": float(repeat_col.loc[i]),
                        "new": float(new_col.loc[i]),
                    }
                    for i in new_col.index
                ]
            },
        )
    ]


# --------------------------------------------------------------------------
# RFM
# --------------------------------------------------------------------------

#: Segment names, and the plain-language meaning behind each.
SEGMENT_LABELS = {
    "champions": "Champions",
    "loyal": "Loyal",
    "new": "New",
    "at_risk": "At risk",
    "lost": "Lost",
    "occasional": "Occasional",
}


def _score(series: pd.Series, reverse: bool = False) -> pd.Series:
    """Quartile score 1 to 4. Quantiles, not fixed thresholds.

    A naira threshold that means "big spender" in one business is a rounding
    error in another, so every score is relative to this business's own
    customers.
    """
    ranked = series.rank(method="average", pct=True)
    if reverse:
        ranked = 1.0 - ranked
    return np.ceil(ranked * 4).clip(1, 4).astype(int)


def rfm_segments(frame: SalesFrame) -> list[Finding]:
    """Sort customers by how recently, how often and how much they buy."""
    if not frame.has_customers:
        return []

    data = frame.data[[DATE, CUSTOMER, REVENUE]].dropna()
    if data.empty or data[CUSTOMER].nunique() < 20:
        return []

    as_of = data[DATE].max()
    grouped = data.groupby(CUSTOMER).agg(
        last_seen=(DATE, "max"),
        first_seen=(DATE, "min"),
        orders=(REVENUE, "size"),
        spend=(REVENUE, "sum"),
    )
    grouped["recency_days"] = (as_of - grouped["last_seen"]).dt.days

    grouped["r"] = _score(grouped["recency_days"], reverse=True)
    grouped["f"] = _score(grouped["orders"])
    grouped["m"] = _score(grouped["spend"])

    def classify(row) -> str:
        r, f = row["r"], row["f"]
        is_new = (as_of - row["first_seen"]).days <= max(
            30, int(grouped["recency_days"].median())
        )
        if r >= 3 and f >= 3:
            return "champions"
        if r >= 3 and is_new and f <= 2:
            return "new"
        if r >= 3:
            return "loyal"
        if r <= 2 and f >= 3:
            return "at_risk"
        if r == 1:
            return "lost"
        return "occasional"

    grouped["segment"] = grouped.apply(classify, axis=1)
    counts = grouped["segment"].value_counts()
    total = int(len(grouped))

    at_risk = int(counts.get("at_risk", 0))
    champions = int(counts.get("champions", 0))
    if total < 20:
        return []

    # Lead with whichever segment is most worth knowing about.
    if at_risk >= max(3, total * 0.05):
        median_gap = float(grouped.loc[grouped["segment"] == "at_risk", "recency_days"].median())
        summary = (
            f"{at_risk} customers who used to buy often have not ordered in "
            f"about {median_gap:.0f} days. They are "
            f"{at_risk / total:.0%} of your customer base and "
            f"{grouped.loc[grouped['segment'] == 'at_risk', 'spend'].sum() / grouped['spend'].sum():.0%} "
            "of what has been spent with you."
        )
        severity = Severity.WATCH
        importance = 0.8
    else:
        summary = (
            f"{champions} of your {total} customers buy both recently and "
            f"often, and account for "
            f"{grouped.loc[grouped['segment'] == 'champions', 'spend'].sum() / grouped['spend'].sum():.0%} "
            "of all spending."
        )
        severity = Severity.NEUTRAL
        importance = 0.6

    segment_rows = [
        {
            "segment": SEGMENT_LABELS.get(name, name),
            "key": name,
            "customers": int(count),
            "share": float(count / total),
            "spend": float(grouped.loc[grouped["segment"] == name, "spend"].sum()),
            "avg_recency_days": float(
                grouped.loc[grouped["segment"] == name, "recency_days"].mean()
            ),
            "avg_orders": float(
                grouped.loc[grouped["segment"] == name, "orders"].mean()
            ),
        }
        for name, count in counts.items()
    ]
    segment_rows.sort(key=lambda r: -r["customers"])

    return [
        Finding(
            id="rfm_segments",
            type=FindingType.CUSTOMER_SEGMENTS,
            summary=summary,
            facts={
                "total_customers": total,
                "segments": segment_rows,
                "at_risk": at_risk,
                "champions": champions,
                "as_of": str(as_of.date()),
            },
            evidence=Evidence(
                method="RFM quartile scoring",
                sample_size=total,
                notes=[
                    "Scores are quartiles within this business, not fixed "
                    "thresholds, so they mean the same thing at any size.",
                    f"Recency measured against the last sale in the file "
                    f"({as_of.date()}), not today.",
                ],
            ),
            severity=severity,
            importance=importance,
            tier=Tier.CUSTOMER,
            chart_data={
                "customers": [
                    {
                        "recency": int(row["recency_days"]),
                        "frequency": int(row["orders"]),
                        "spend": float(row["spend"]),
                        "segment": SEGMENT_LABELS.get(row["segment"], row["segment"]),
                    }
                    # Cap the payload; a quadrant does not need 5,000 dots.
                    for _, row in grouped.sample(
                        min(400, len(grouped)), random_state=0
                    ).iterrows()
                ],
                "segments": segment_rows,
            },
        )
    ]


# --------------------------------------------------------------------------
# Cohort retention
# --------------------------------------------------------------------------


def cohort_retention(frame: SalesFrame) -> list[Finding]:
    """Does each month's intake stick, or is the bucket leaking?"""
    if not frame.has_customers:
        return []

    data = frame.data[[DATE, CUSTOMER]].dropna()
    if data.empty or data[CUSTOMER].nunique() < 30:
        return []

    first = data.groupby(CUSTOMER)[DATE].min()
    data = data.assign(
        cohort=data[CUSTOMER].map(first).dt.to_period("M"),
        period=data[DATE].dt.to_period("M"),
    )
    data["age"] = (data["period"] - data["cohort"]).apply(lambda x: x.n)

    sizes = data.groupby("cohort")[CUSTOMER].nunique()
    usable = sizes[sizes >= MIN_COHORT_SIZE].index
    if len(usable) < MIN_COHORTS:
        return []
    data = data[data["cohort"].isin(usable)]

    table = (
        data.groupby(["cohort", "age"])[CUSTOMER]
        .nunique()
        .unstack(fill_value=0)
    )
    base = table[0].replace(0, np.nan)
    rates = table.div(base, axis=0)

    # Average retention by age, across cohorts that have actually aged that far.
    curve: list[dict] = []
    for age in sorted(c for c in rates.columns if c > 0):
        # Only cohorts old enough to have reached this age.
        eligible = [
            cohort
            for cohort in rates.index
            if (data["period"].max() - cohort).n >= age
        ]
        if len(eligible) < 2:
            continue
        value = float(rates.loc[eligible, age].mean())
        if np.isfinite(value):
            curve.append({"age": int(age), "retention": value})

    if len(curve) < 2:
        return []

    month_one = curve[0]["retention"]
    furthest = curve[-1]

    return [
        Finding(
            id="cohort_retention",
            type=FindingType.COHORT_RETENTION,
            summary=(
                f"Of customers who buy once, about {month_one:.0%} come back the "
                f"following month, and {furthest['retention']:.0%} are still "
                f"buying {furthest['age']} months later."
            ),
            facts={
                "month_one_retention": month_one,
                "curve": curve,
                "furthest_age": furthest["age"],
                "furthest_retention": furthest["retention"],
                "cohorts": int(len(usable)),
                "customers": int(data[CUSTOMER].nunique()),
            },
            evidence=Evidence(
                method="cohort retention by month of first purchase",
                sample_size=int(data[CUSTOMER].nunique()),
                notes=[
                    f"Cohorts smaller than {MIN_COHORT_SIZE} customers are "
                    "excluded as too small to read.",
                    "Each age is averaged only over cohorts old enough to have "
                    "reached it, so recent cohorts do not drag the curve down.",
                ],
            ),
            severity=Severity.NEUTRAL,
            importance=0.62,
            tier=Tier.CUSTOMER,
            chart_data={
                "curve": curve,
                "cohorts": [
                    {
                        "cohort": str(cohort),
                        "size": int(table.loc[cohort, 0]),
                        "rates": [
                            {"age": int(age), "retention": float(rates.loc[cohort, age])}
                            for age in sorted(rates.columns)
                            if np.isfinite(rates.loc[cohort, age])
                            and (data["period"].max() - cohort).n >= age
                        ],
                    }
                    for cohort in rates.index
                ],
            },
        )
    ]


# --------------------------------------------------------------------------
# Basket analysis
# --------------------------------------------------------------------------


def basket_analysis(frame: SalesFrame) -> list[Finding]:
    """Which products get bought together, beyond what chance explains.

    Lift is ``P(A and B) / (P(A) x P(B))``: how much more often a pair shows up
    together than if the two were independent. Two guards keep it honest — a
    minimum number of baskets so a pair that co-occurred twice cannot post a
    huge multiple, and an FDR correction across every pair tested, because a
    thirty-product catalogue is four hundred and thirty-five comparisons.
    """
    key = _basket_key(frame)
    if key is None:
        return []

    data = frame.data[[PRODUCT]].copy()
    if key == ORDER:
        data["basket"] = frame.data[ORDER]
        basket_note = "one order"
    else:
        # Same customer, same day.
        data["basket"] = (
            frame.data[CUSTOMER].astype(str)
            + "|"
            + frame.data[DATE].dt.date.astype(str)
        )
        basket_note = "one customer on one day"
    data = data.dropna()
    if data.empty:
        return []

    baskets = data.groupby("basket")[PRODUCT].apply(set)
    # A basket of one tells us nothing about pairing.
    baskets = baskets[baskets.apply(len) >= 2]
    n_baskets = int(len(baskets))
    if n_baskets < 30:
        return []

    all_baskets = data.groupby("basket")[PRODUCT].apply(set)
    total_baskets = int(len(all_baskets))
    counts: dict[str, int] = {}
    for items in all_baskets:
        for item in items:
            counts[item] = counts.get(item, 0) + 1

    pair_counts: dict[tuple[str, str], int] = {}
    for items in baskets:
        ordered = sorted(items)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1

    if not pair_counts:
        return []

    support_floor = max(MIN_PAIR_SUPPORT, int(total_baskets * MIN_PAIR_SUPPORT_SHARE))

    from scipy import stats as scipy_stats

    candidates: list[dict] = []
    for (a, b), together in pair_counts.items():
        if together < support_floor:
            continue
        count_a, count_b = counts.get(a, 0), counts.get(b, 0)
        if count_a == 0 or count_b == 0:
            continue
        expected = count_a * count_b / total_baskets
        if expected <= 0:
            continue
        lift = together / expected

        # Fisher's exact on the 2x2 basket table: with A and B, with A not B,
        # with B not A, with neither. Exact because pair counts are small.
        only_a = count_a - together
        only_b = count_b - together
        neither = total_baskets - together - only_a - only_b
        if min(only_a, only_b, neither) < 0:
            continue
        try:
            _, p = scipy_stats.fisher_exact(
                [[together, only_a], [only_b, neither]], alternative="greater"
            )
        except ValueError:
            continue
        if not np.isfinite(p):
            continue

        candidates.append(
            {
                "a": a,
                "b": b,
                "together": int(together),
                "lift": float(lift),
                "p_value": float(p),
                "support": float(together / total_baskets),
            }
        )

    if not candidates:
        return []

    _, adjusted = stats.benjamini_hochberg([c["p_value"] for c in candidates])
    for candidate, adj in zip(candidates, adjusted):
        candidate["adjusted_p"] = float(adj)

    strong = [
        c
        for c in candidates
        if c["adjusted_p"] < stats.ALPHA and c["lift"] >= MIN_LIFT
    ]
    if not strong:
        return []

    strong.sort(key=lambda c: -c["lift"])
    best = strong[0]

    return [
        Finding(
            id="basket_analysis",
            type=FindingType.BASKET,
            summary=(
                f"{best['a']} and {best['b']} are bought together "
                f"{best['lift']:.1f} times more often than they would be by "
                f"chance, across {best['together']} baskets."
            ),
            facts={
                "pairs": strong[:8],
                "strongest": best,
                "pairs_tested": len(candidates),
                "baskets": total_baskets,
                "basket_definition": basket_note,
                "support_floor": support_floor,
                "item_count": len(strong[:8]),
            },
            evidence=Evidence(
                method="lift with Fisher's exact test",
                p_value=best["p_value"],
                adjusted_p=best["adjusted_p"],
                sample_size=total_baskets,
                correction="Benjamini-Hochberg FDR",
                notes=[
                    f"A basket is {basket_note}.",
                    f"{len(candidates)} pairs tested together; p-values "
                    "corrected across the whole family.",
                    f"Pairs appearing in fewer than {support_floor} baskets are "
                    "excluded, because a large multiple on two co-occurrences "
                    "means nothing.",
                ],
            ),
            severity=Severity.NEUTRAL,
            importance=0.58,
            tier=Tier.CUSTOMER if key != ORDER else Tier.CORE,
            chart_data={
                "bars": [
                    {
                        "label": f"{c['a']} + {c['b']}",
                        "value": float(c["lift"]),
                        "share": float(c["support"]),
                    }
                    for c in strong[:8]
                ]
            },
        )
    ]
