"""Pillar 0: the non-obvious layer, which is the real product.

Every function here takes a :class:`~busylab.analysis.dataset.SalesFrame` and
returns findings. Nothing here formats English beyond a plain factual sentence,
nothing here decides anything for the business, and nothing here runs if the
data cannot support it.

The distinction that matters throughout: "Product 6 sells the most" is useless,
they packed the boxes (spec 2). What earns a finding is margin reality,
statistical significance, seasonality-adjusted movement, concentration risk and
decomposition, because those are the things a human cannot see by eyeballing
rows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..findings import Evidence, Finding, FindingType, Severity
from ..roles import Tier
from . import stats
from .dataset import (
    MARGIN,
    PRODUCT,
    PROFIT,
    QUANTITY,
    REVENUE,
    UNIT_PRICE,
    SalesFrame,
)


#: A movement smaller than this is not worth a business owner's attention,
#: however clean the statistics are. Guards against a large sample making a
#: trivial drift "significant".
MATERIAL_CHANGE = 0.10


def _money(value: float) -> str:
    """Compact money for the fallback sentence. Narration may re-render."""
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:,.1f}m"
    if magnitude >= 1_000:
        return f"{value / 1_000:,.1f}k"
    return f"{value:,.0f}"


def _pct(value: float) -> str:
    return f"{abs(value) * 100:.0f}%"


# --------------------------------------------------------------------------
# Trend versus noise
# --------------------------------------------------------------------------


def revenue_trend(frame: SalesFrame) -> list[Finding]:
    """Is revenue actually moving, and is it the calendar or the business?

    Seasonality is removed before the trend is tested wherever there is enough
    history, so a normal December dip is not reported as a decline (spec 5).
    """
    freq = frame.natural_frequency()
    series = frame.by_period(freq=freq, value=REVENUE)
    if len(series) < stats.MIN_POINTS:
        return []

    strength = stats.seasonal_strength(series)
    adjusted = stats.deseasonalize(series)
    tested = adjusted if adjusted is not None else series
    result = stats.trend_test(tested)
    if result is None:
        return []

    first, last = float(tested.iloc[0]), float(tested.iloc[-1])

    # Measure the movement from the fitted trend, not from the first and last
    # points. Two individual periods are noisy, and comparing them is exactly
    # the eyeballing this analysis exists to replace: on a flat business they
    # can differ by 20% while the underlying level has not moved at all.
    level = float(tested.mean())
    fitted_change = result.statistic * (len(tested) - 1)
    change = fitted_change / abs(level) if level else None
    direction = "down" if result.statistic < 0 else "up"
    seasonally_adjusted = adjusted is not None

    chart = {
        "series": [
            {"period": str(i.date()), "value": float(v)} for i, v in series.items()
        ],
        "trend_per_period": result.statistic,
        "seasonally_adjusted": seasonally_adjusted,
    }

    facts = {
        "direction": direction,
        "change_pct": change,
        "periods": int(len(series)),
        "first_value": first,
        "last_value": last,
        "fitted_change": fitted_change,
        "trend_per_period": result.statistic,
        "seasonally_adjusted": seasonally_adjusted,
        "seasonal_strength": strength,
        "frequency": freq,
    }

    evidence = Evidence(
        method=result.method,
        p_value=result.p_value,
        sample_size=result.sample_size,
        confidence_low=result.ci_low,
        confidence_high=result.ci_high,
        notes=(
            ["Seasonal pattern removed before testing."]
            if seasonally_adjusted
            else ["Not enough history to separate seasonality."]
        ),
    )

    # Statistical significance and business significance are different things.
    # With enough points a trivial drift becomes detectable, and reporting it
    # as a trend is precisely the impressive-looking noise the spec warns
    # against. A movement must clear both bars before it is called a trend.
    material = change is not None and abs(change) >= MATERIAL_CHANGE

    if result.significant and material:
        summary = (
            f"Revenue is {direction} {_pct(change)} across the period, "
            "and that movement is larger than this business's normal variation."
        )
        severity = Severity.URGENT if direction == "down" else Severity.GOOD
        importance = min(0.95, 0.6 + abs(change or 0) * 0.6)
        finding_type = FindingType.TREND
    elif result.significant and not material:
        summary = (
            f"Revenue drifts slightly {direction} over the period, by too "
            "little to change the shape of the business."
        )
        severity = Severity.NEUTRAL
        importance = 0.25
        finding_type = FindingType.NOISE
    elif result.worth_a_look and material:
        summary = (
            f"Revenue leans {direction} over the period, but the movement is "
            "not clearly outside normal variation yet."
        )
        severity = Severity.WATCH
        importance = 0.45
        finding_type = FindingType.NOISE
    else:
        summary = (
            "Revenue has moved around, but the ups and downs sit inside this "
            "business's normal variation rather than forming a trend."
        )
        severity = Severity.NEUTRAL
        importance = 0.3
        finding_type = FindingType.NOISE

    facts["material"] = material

    return [
        Finding(
            id="revenue_trend",
            type=finding_type,
            summary=summary,
            facts=facts,
            evidence=evidence,
            severity=severity,
            importance=importance,
            chart_data=chart,
        )
    ]


def seasonality(frame: SalesFrame) -> list[Finding]:
    """Report a repeating annual shape, when one genuinely exists."""
    series = frame.by_period(freq="MS", value=REVENUE)
    strength = stats.seasonal_strength(series)
    if strength is None or strength < 0.35 or len(series) < 24:
        return []

    by_month = series.groupby(series.index.month).mean()
    overall = float(by_month.mean())
    if overall <= 0:
        return []
    peak_month = int(by_month.idxmax())
    trough_month = int(by_month.idxmin())
    peak_lift = float(by_month.max() / overall - 1)
    trough_dip = float(by_month.min() / overall - 1)

    names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]

    return [
        Finding(
            id="seasonality",
            type=FindingType.SEASONALITY,
            summary=(
                f"Sales follow a repeating yearly shape: {names[peak_month - 1]} "
                f"runs about {_pct(peak_lift)} above the average month and "
                f"{names[trough_month - 1]} about {_pct(trough_dip)} below."
            ),
            facts={
                "strength": strength,
                "peak_month": peak_month,
                "peak_month_name": names[peak_month - 1],
                "peak_lift": peak_lift,
                "trough_month": trough_month,
                "trough_month_name": names[trough_month - 1],
                "trough_dip": trough_dip,
                "monthly_index": {
                    names[m - 1]: float(v / overall) for m, v in by_month.items()
                },
            },
            evidence=Evidence(
                method="STL seasonal decomposition",
                sample_size=int(len(series)),
                notes=["Strength is the share of variation explained by the calendar."],
            ),
            severity=Severity.NEUTRAL,
            importance=0.5,
            chart_data={
                "series": [
                    {"period": str(i.date()), "value": float(v)}
                    for i, v in series.items()
                ],
                "monthly_index": {
                    names[m - 1]: float(v / overall) for m, v in by_month.items()
                },
            },
        )
    ]


# --------------------------------------------------------------------------
# Margin reality
# --------------------------------------------------------------------------


def margin_reality(frame: SalesFrame) -> list[Finding]:
    """The top revenue product is often not the top profit product.

    This is the headline insight of spec 5 and the clearest example of a fact
    the owner cannot get by looking at their own sheet.
    """
    if not frame.has_profit:
        return []

    revenue = frame.by_product(REVENUE)
    profit = frame.by_product(PROFIT)
    if len(revenue) < 2 or profit.empty:
        return []

    top_seller = str(revenue.index[0])
    ranked_profit = profit.sort_values(ascending=False)
    top_earner = str(ranked_profit.index[0])

    margins = (
        frame.data.groupby(PRODUCT)
        .apply(
            lambda g: float(g[PROFIT].sum() / g[REVENUE].sum())
            if g[REVENUE].sum() > 0
            else np.nan,
            include_groups=False,
        )
        .dropna()
    )

    points = [
        {
            "product": str(p),
            "revenue": float(revenue.get(p, 0.0)),
            "profit": float(profit.get(p, 0.0)),
            "margin": float(margins.get(p, np.nan)),
        }
        for p in revenue.index
    ]

    findings: list[Finding] = []

    if top_seller != top_earner:
        rank = int(list(ranked_profit.index).index(top_seller)) + 1
        ordinal = {1: "first", 2: "second", 3: "third"}.get(rank, f"{rank}th")
        findings.append(
            Finding(
                id="margin_reality",
                type=FindingType.TENSION,
                summary=(
                    f"{top_seller} brings in the most revenue but is your "
                    f"{ordinal} most profitable product. {top_earner} earns the "
                    "most once cost is taken out."
                ),
                facts={
                    "top_seller": top_seller,
                    "top_seller_revenue": float(revenue.iloc[0]),
                    "top_seller_profit": float(profit.get(top_seller, 0.0)),
                    "top_seller_margin": float(margins.get(top_seller, np.nan)),
                    "top_earner": top_earner,
                    "top_earner_profit": float(ranked_profit.iloc[0]),
                    "top_earner_margin": float(margins.get(top_earner, np.nan)),
                    "profit_rank_of_top_seller": rank,
                    "item_count": int(len(revenue)),
                },
                evidence=Evidence(
                    method="contribution margin by product",
                    sample_size=frame.n_rows,
                    notes=[
                        "Margin is revenue minus cost, divided by revenue, "
                        f"using cost read as a {frame.cost_basis or 'line total'}."
                    ],
                ),
                severity=Severity.WATCH,
                importance=0.85,
                tier=Tier.MARGIN,
                chart_data={"points": points},
            )
        )

    # A product that sells well and loses money is worth stating plainly.
    losers = margins[margins < 0]
    if not losers.empty:
        worst = str(losers.idxmin())
        share = float(revenue.get(worst, 0.0) / revenue.sum()) if revenue.sum() else 0.0
        findings.append(
            Finding(
                id="loss_making_product",
                type=FindingType.TENSION,
                summary=(
                    f"{worst} sells at a loss: its costs exceed its revenue by "
                    f"{_pct(abs(float(losers.min())))} of what it brings in, and "
                    f"it is {_pct(share)} of total revenue."
                ),
                facts={
                    "product": worst,
                    "margin": float(losers.min()),
                    "revenue_share": share,
                    "profit": float(profit.get(worst, 0.0)),
                    "item_count": int(len(revenue)),
                },
                evidence=Evidence(
                    method="contribution margin by product",
                    sample_size=frame.n_rows,
                ),
                severity=Severity.URGENT,
                importance=0.9,
                tier=Tier.MARGIN,
                chart_data={"points": points},
            )
        )

    return findings


# --------------------------------------------------------------------------
# Concentration and dependency risk
# --------------------------------------------------------------------------


def concentration_risk(frame: SalesFrame) -> list[Finding]:
    """How much of the business rests on how few products.

    Reported for profit where cost is known, because dependency on the thing
    that *earns* is the sharper risk, and for revenue otherwise.
    """
    use_profit = frame.has_profit
    metric = PROFIT if use_profit else REVENUE
    label = "profit" if use_profit else "revenue"

    totals = frame.by_product(metric)
    totals = totals[totals > 0]
    if len(totals) < 2:
        return []

    summary_stats = stats.concentration(totals)
    if not summary_stats:
        return []

    top1 = summary_stats["top1_share"]
    top2 = summary_stats["top2_share"]
    items = summary_stats["items"]

    # Only interesting when it is actually concentrated. An evenly spread
    # business does not need to be told it is evenly spread.
    if top1 < 0.35 and top2 < 0.6:
        return []

    leader = str(totals.index[0])
    if top1 >= 0.5:
        text = (
            f"{_pct(top1)} of {label} comes from one product, {leader}."
        )
        importance, severity = 0.88, Severity.WATCH
    else:
        text = (
            f"{_pct(top2)} of {label} comes from two products, "
            f"{leader} and {totals.index[1]}."
        )
        importance, severity = 0.75, Severity.WATCH

    return [
        Finding(
            id="concentration",
            type=FindingType.CONCENTRATION,
            summary=(
                text + " A change in either direction there moves the whole business."
            ),
            facts={
                "metric": label,
                "leader": leader,
                "top1_share": top1,
                "top2_share": top2,
                "top3_share": summary_stats["top3_share"],
                "herfindahl": summary_stats["herfindahl"],
                "items_for_half": summary_stats["items_for_half"],
                "item_count": items,
                "total": summary_stats["total"],
            },
            evidence=Evidence(
                method="share of total and Herfindahl index",
                sample_size=frame.n_rows,
            ),
            severity=severity,
            importance=importance,
            tier=Tier.MARGIN if use_profit else Tier.CORE,
            chart_data={
                "slices": [
                    {"label": str(p), "value": float(v), "share": float(v / totals.sum())}
                    for p, v in totals.items()
                ]
            },
        )
    ]


# --------------------------------------------------------------------------
# Decomposition: why the number moved
# --------------------------------------------------------------------------


def revenue_decomposition(frame: SalesFrame) -> list[Finding]:
    """Break a revenue change into the products that caused it.

    The composition of a number is where the decision lives (spec 5), so a
    20 percent fall is reported as which products moved and by how much,
    summing exactly to the total change.
    """
    freq = frame.natural_frequency()
    matrix = frame.product_period(freq=freq, value=REVENUE)
    if matrix.empty or len(matrix) < 4:
        return []

    half = len(matrix) // 2
    earlier = matrix.iloc[:half].sum()
    later = matrix.iloc[half:].sum()

    # Compare like with like when the halves differ in length.
    earlier = earlier / max(half, 1)
    later = later / max(len(matrix) - half, 1)

    delta = (later - earlier).sort_values()
    total_delta = float(delta.sum())
    base = float(earlier.sum())
    if base <= 0 or abs(total_delta) < base * 0.03:
        return []  # nothing moved enough to decompose

    contributions = [
        {"label": str(p), "change": float(v), "share_of_change": float(v / total_delta)}
        for p, v in delta.items()
        if abs(v) > 0
    ]
    contributions.sort(key=lambda c: c["change"])

    biggest = contributions[0] if total_delta < 0 else contributions[-1]
    direction = "fell" if total_delta < 0 else "rose"
    share = abs(biggest["share_of_change"])

    return [
        Finding(
            id="revenue_decomposition",
            type=FindingType.DECOMPOSITION,
            summary=(
                f"Revenue per period {direction} by {_money(abs(total_delta))} "
                f"between the first and second half of the period. "
                f"{biggest['label']} accounts for {_pct(share)} of that move."
            ),
            facts={
                "direction": direction,
                "total_change": total_delta,
                "change_pct": stats.safe_pct_change(float(later.sum()), base),
                "baseline": base,
                "largest_mover": biggest["label"],
                "largest_mover_change": biggest["change"],
                "largest_mover_share": biggest["share_of_change"],
                "contributions": contributions,
                "item_count": int(len(contributions)),
            },
            evidence=Evidence(
                method="per-product contribution to change",
                sample_size=int(len(matrix)),
                notes=["Halves are averaged per period so unequal lengths compare."],
            ),
            severity=Severity.WATCH if total_delta < 0 else Severity.GOOD,
            importance=0.8,
            chart_data={
                "start": {"label": "Earlier average", "value": base},
                "steps": contributions,
                "end": {"label": "Later average", "value": float(later.sum())},
            },
        )
    ]


def dimension_decomposition(frame: SalesFrame) -> list[Finding]:
    """Was the change spread across the business, or concentrated in one slice?

    "Product 3 is dying online, fine in store" (spec 3.3) is a different fact
    from "revenue is down", and it is the one that locates the problem. Only
    raised when a single slice carries a clearly disproportionate share of the
    move, since a change spread evenly across channels is not a channel story.
    """
    findings: list[Finding] = []

    for column, label in frame.dimensions.items():
        if column not in frame.data.columns:
            continue
        levels = frame.data[column].dropna().nunique()
        if levels < 2 or levels > 12:
            continue

        freq = frame.natural_frequency()
        pivot = frame.data.pivot_table(
            index=pd.Grouper(key="date", freq=freq),
            columns=column,
            values=REVENUE,
            aggfunc="sum",
        ).fillna(0.0)
        if len(pivot) < 4:
            continue

        half = len(pivot) // 2
        earlier = pivot.iloc[:half].sum() / max(half, 1)
        later = pivot.iloc[half:].sum() / max(len(pivot) - half, 1)
        delta = later - earlier
        total = float(delta.sum())
        base = float(earlier.sum())
        if base <= 0 or abs(total) < base * 0.05:
            continue

        moves = delta.sort_values()
        biggest = moves.index[0] if total < 0 else moves.index[-1]
        biggest_change = float(moves.loc[biggest])
        share = biggest_change / total if total else 0.0

        # Only a story when one slice dominates the move.
        if share < 0.6:
            continue

        steady = [
            str(k)
            for k, v in delta.items()
            if str(k) != str(biggest)
            and earlier.get(k, 0) > 0
            and abs(v / earlier[k]) < 0.1
        ]

        direction = "fell" if total < 0 else "grew"
        tail = (
            f" {', '.join(steady)} held roughly steady."
            if steady
            else ""
        )

        findings.append(
            Finding(
                id=f"decomposition_{column}",
                type=FindingType.DECOMPOSITION,
                summary=(
                    f"The change is concentrated in one {label}: {biggest} "
                    f"{direction} by {_money(abs(biggest_change))} per period, "
                    f"which is {_pct(share)} of the total move.{tail}"
                ),
                facts={
                    "dimension": label,
                    "column": column,
                    "driver": str(biggest),
                    "driver_change": biggest_change,
                    "driver_share_of_change": share,
                    "total_change": total,
                    "steady_levels": steady,
                    "contributions": [
                        {"label": str(k), "change": float(v)} for k, v in moves.items()
                    ],
                    "item_count": int(len(moves)),
                },
                evidence=Evidence(
                    method=f"contribution to change by {label}",
                    sample_size=int(len(pivot)),
                ),
                severity=Severity.URGENT if total < 0 else Severity.GOOD,
                importance=0.82,
                tier=Tier.SEGMENT,
                chart_data={
                    "start": {"label": "Earlier average", "value": base},
                    "steps": [
                        {"label": str(k), "change": float(v)} for k, v in moves.items()
                    ],
                    "end": {"label": "Later average", "value": float(later.sum())},
                },
            )
        )

    return findings


def price_volume_split(frame: SalesFrame) -> list[Finding]:
    """Did revenue move because of price, or because of units sold?

    Two businesses with the same revenue fall are in completely different
    situations depending on the answer, and it is invisible in the totals.
    """
    if not frame.has(QUANTITY) or not frame.has(UNIT_PRICE):
        return []

    freq = frame.natural_frequency()
    data = frame.data.set_index("date")
    quantity = data[QUANTITY].resample(freq).sum()
    revenue = data[REVENUE].resample(freq).sum()
    if len(quantity) < 4:
        return []

    half = len(quantity) // 2
    q0 = float(quantity.iloc[:half].mean())
    q1 = float(quantity.iloc[half:].mean())
    r0 = float(revenue.iloc[:half].mean())
    r1 = float(revenue.iloc[half:].mean())
    if q0 <= 0 or r0 <= 0:
        return []

    p0, p1 = r0 / q0, r1 / q1 if q1 else 0.0
    total_change = r1 - r0
    if abs(total_change) < r0 * 0.03:
        return []

    # Standard split: volume effect at old price, price effect at new volume.
    volume_effect = (q1 - q0) * p0
    price_effect = (p1 - p0) * q1

    dominant = "volume" if abs(volume_effect) >= abs(price_effect) else "price"
    wording = (
        "fewer units rather than lower prices"
        if dominant == "volume" and total_change < 0
        else "lower prices rather than fewer units"
        if dominant == "price" and total_change < 0
        else "more units rather than higher prices"
        if dominant == "volume"
        else "higher prices rather than more units"
    )

    return [
        Finding(
            id="price_volume_split",
            type=FindingType.DECOMPOSITION,
            summary=(
                f"The revenue change is mostly {wording}: "
                f"units moved {_pct(stats.safe_pct_change(q1, q0) or 0)} and "
                f"average price moved {_pct(stats.safe_pct_change(p1, p0) or 0)}."
            ),
            facts={
                "total_change": total_change,
                "volume_effect": volume_effect,
                "price_effect": price_effect,
                "dominant": dominant,
                "quantity_before": q0,
                "quantity_after": q1,
                "avg_price_before": p0,
                "avg_price_after": p1,
                "quantity_change_pct": stats.safe_pct_change(q1, q0),
                "price_change_pct": stats.safe_pct_change(p1, p0),
            },
            evidence=Evidence(
                method="price and volume effect decomposition",
                sample_size=int(len(quantity)),
            ),
            severity=Severity.WATCH if total_change < 0 else Severity.GOOD,
            importance=0.72,
            chart_data={
                "start": {"label": "Earlier average", "value": r0},
                "steps": [
                    {"label": "Units", "change": volume_effect},
                    {"label": "Price", "change": price_effect},
                ],
                "end": {"label": "Later average", "value": r1},
            },
        )
    ]


# --------------------------------------------------------------------------
# The commodity layer: necessary, not the moat
# --------------------------------------------------------------------------


def product_ranking(frame: SalesFrame) -> list[Finding]:
    """Plain rankings. Table stakes (spec 5), ranked low on purpose."""
    revenue = frame.by_product(REVENUE)
    if len(revenue) < 2:
        return []
    total = float(revenue.sum())
    if total <= 0:
        return []

    bars = [
        {"label": str(p), "value": float(v), "share": float(v / total)}
        for p, v in revenue.items()
    ]
    return [
        Finding(
            id="product_ranking",
            type=FindingType.RANKING,
            summary=(
                f"{revenue.index[0]} is the largest product by revenue at "
                f"{_money(float(revenue.iloc[0]))}, out of {len(revenue)} products."
            ),
            facts={
                "leader": str(revenue.index[0]),
                "leader_revenue": float(revenue.iloc[0]),
                "total_revenue": total,
                "item_count": int(len(revenue)),
                "ranking": bars,
            },
            evidence=Evidence(method="sum of revenue by product", sample_size=frame.n_rows),
            severity=Severity.NEUTRAL,
            importance=0.2,  # they packed the boxes; they know this already
            chart_data={"bars": bars},
        )
    ]
