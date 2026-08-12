"""Analyses that need nothing beyond date, product, quantity and price.

Added after watching the product run against real files. The story was thin, and
the reason was only half about missing analyses: those files carried no cost and
no customer id, so the margin and customer pillars never ran at all. But the core
pillar was genuinely thin too. Almost every finding was a comparison of the form
"the biggest X against the smallest X", which is one thing an analyst does among
many, and the least interesting of them.

What is here is chosen for the opposite quality: each one answers a question a
business owner would actually ask, and none can be got at by sorting a column.

* **Concentration classes.** Not "which product is biggest" but "how few products
  is this business actually running on", which is the question behind it.
* **Price against volume, per product.** Whether charging more sold fewer, and
  whether the trade was worth making. This is the analysis people pay for and
  nothing else in the engine attempts it.
* **Lifecycle.** Which products are new, growing, fading, or have quietly stopped
  selling altogether. A product that stops appearing is invisible to every
  ranking, because rankings only show what is there.
* **Order size distribution.** Averages hide their own shape. Half the orders
  being under a figure the average never mentions is a different business from
  the one the average describes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..findings import Evidence, Finding, FindingType, Severity
from ..roles import Tier
from . import stats
from .dataset import DATE, PRODUCT, QUANTITY, REVENUE, UNIT_PRICE, SalesFrame

#: Share of revenue used to define the "vital few". 80% is the convention and
#: the point is the count that reaches it, not the threshold itself.
PARETO_SHARE = 0.80

#: Below this share of revenue a product is a tail item rather than a line.
TAIL_SHARE = 0.01

#: Periods a product must have traded in before its price behaviour is worth
#: testing. Fewer than this and any slope is noise.
MIN_PRICE_POINTS = 6

#: Price has to actually move before its effect on volume can be seen.
MIN_PRICE_VARIATION = 0.05

#: Periods of silence after regular trading before a product counts as stopped.
DORMANT_PERIODS = 2


def _money(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:,.1f}m"
    if magnitude >= 1_000:
        return f"{value / 1_000:,.1f}k"
    return f"{value:,.0f}"


# --------------------------------------------------------------------------
# How few products the business runs on
# --------------------------------------------------------------------------


def concentration_classes(frame: SalesFrame) -> list[Finding]:
    """How much of the business rests on how few products.

    Distinct from the existing concentration finding, which reports the single
    largest share. The question an owner asks is not "what is my biggest
    product" - they know - it is "how much of this rests on a handful of
    things", and the honest answer is a count.

    The tail matters as much as the head. Products earning under one percent
    each still take up shelf space, order admin and attention, and their number
    is usually a surprise.
    """
    if not frame.has(PRODUCT) or not frame.has(REVENUE):
        return []

    by_product = (
        frame.data.groupby(PRODUCT)[REVENUE].sum().sort_values(ascending=False)
    )
    by_product = by_product[by_product > 0]
    if len(by_product) < 4:
        return []

    total = float(by_product.sum())
    if total <= 0:
        return []

    shares = by_product / total
    cumulative = shares.cumsum()
    vital = int((cumulative < PARETO_SHARE).sum()) + 1
    vital = min(vital, len(by_product))
    vital_share = float(cumulative.iloc[vital - 1])
    tail = shares[shares < TAIL_SHARE]

    facts = {
        "products": int(len(by_product)),
        "vital_count": vital,
        "vital_share": vital_share,
        "vital_products": [str(p) for p in by_product.index[:vital]],
        "tail_count": int(len(tail)),
        "tail_share": float(tail.sum()),
        "total_revenue": total,
    }

    portion = vital / len(by_product)
    summary = (
        f"{vital} of your {len(by_product)} products bring in "
        f"{vital_share:.0%} of the money."
    )
    if len(tail):
        summary += (
            f" At the other end, {len(tail)} of them earn under 1% each, "
            f"{tail.sum():.0%} between them."
        )

    # A narrow base is worth attention; a broad one is worth knowing and no more.
    severity = Severity.WATCH if portion <= 0.25 else Severity.NEUTRAL

    return [
        Finding(
            id="concentration_classes",
            type=FindingType.CONCENTRATION,
            summary=summary,
            facts=facts,
            evidence=Evidence(
                method="share of revenue by product, cumulative",
                sample_size=int(len(by_product)),
                notes=[
                    "Descriptive: a count and a share, with no test behind it."
                ],
            ),
            severity=severity,
            importance=0.62 if portion <= 0.25 else 0.4,
            chart_data={
                "slices": [
                    {"label": str(name), "value": float(value)}
                    for name, value in by_product.head(8).items()
                ],
                "item_count": int(len(by_product)),
            },
        )
    ]


# --------------------------------------------------------------------------
# Did charging more sell fewer
# --------------------------------------------------------------------------


def price_response(frame: SalesFrame) -> list[Finding]:
    """Whether a product's price moving changed how much of it sold.

    The one analysis here that a business would pay an analyst for, and the one
    that most needs care in how it is worded.

    What is measured: across periods, within a single product, how units sold
    varied with average selling price. Both in logs, so the slope reads as a
    percentage response to a percentage change, which is what elasticity means.

    What is *not* measured: cause. Price and volume both move with the season, a
    promotion moves both at once, and a product running low can show a high price
    and low volume without one having produced the other. So the finding says
    "when the price was higher, fewer sold" and never "raising the price will
    reduce sales". The distinction is the difference between a useful observation
    and a bad decision.

    Every product is tested and the whole family is corrected together, because
    testing twenty products and reporting the strongest is the same error as
    testing twenty hypotheses and keeping the winner.
    """
    if not all(frame.has(c) for c in (PRODUCT, QUANTITY, UNIT_PRICE)):
        return []

    freq = frame.natural_frequency()
    data = frame.data[[DATE, PRODUCT, QUANTITY, UNIT_PRICE, REVENUE]].dropna()
    if data.empty:
        return []

    grouped = data.groupby(
        [PRODUCT, pd.Grouper(key=DATE, freq=freq)]
    ).agg(units=(QUANTITY, "sum"), revenue=(REVENUE, "sum"))
    grouped["price"] = grouped["revenue"] / grouped["units"].replace(0, np.nan)

    candidates: list[tuple[str, stats.TestResult, float, float, int]] = []
    for product, rows in grouped.groupby(level=0):
        rows = rows.dropna(subset=["price", "units"])
        rows = rows[(rows["units"] > 0) & (rows["price"] > 0)]
        if len(rows) < MIN_PRICE_POINTS:
            continue

        price = rows["price"].to_numpy(float)
        units = rows["units"].to_numpy(float)
        spread = float(price.std() / price.mean()) if price.mean() else 0.0
        if spread < MIN_PRICE_VARIATION:
            continue  # the price never moved, so nothing can be read from it

        result = stats.slope_test(np.log(price), np.log(units))
        if result is None:
            continue
        candidates.append((str(product), result, result.statistic, spread, len(rows)))

    if not candidates:
        return []

    corrected = stats.correct_family([c[1] for c in candidates])

    findings: list[Finding] = []
    for (product, _, elasticity, spread, points), result in zip(candidates, corrected):
        if not result.significant:
            continue
        # Below this the response is real but too small to change a decision.
        if abs(elasticity) < 0.3:
            continue

        # Elasticity steeper than -1 means revenue moved against the price.
        revenue_follows_price = elasticity > -1.0
        if elasticity < 0:
            direction = (
                "and total takings still rose, because the price rose by more "
                "than the volume fell"
                if revenue_follows_price
                else "and total takings fell with it, because volume dropped by "
                "more than the price gained"
            )
            summary = (
                f"When {product} cost more, fewer sold: about "
                f"{abs(elasticity) * 10:.0f}% fewer units for every 10% on the "
                f"price, {direction}."
            )
            severity = (
                Severity.NEUTRAL if revenue_follows_price else Severity.WATCH
            )
        else:
            summary = (
                f"{product} sold more at higher prices, not less. That is "
                f"usually demand and price rising together rather than one "
                f"causing the other, so it is a thing to look at rather than "
                f"a lever to pull."
            )
            severity = Severity.NEUTRAL

        findings.append(
            Finding(
                id=f"price_response_{product}",
                type=FindingType.ELASTICITY,
                summary=summary,
                facts={
                    "product": product,
                    "elasticity": elasticity,
                    "price_variation": spread,
                    "periods": points,
                    "revenue_follows_price": revenue_follows_price,
                    "points": [
                        {"price": float(p), "units": float(u)}
                        for p, u in zip(
                            grouped.loc[product]["price"].dropna().tolist(),
                            grouped.loc[product]["units"].dropna().tolist(),
                        )
                    ][:60],
                },
                evidence=Evidence(
                    method="log-log slope of units against price, within product",
                    p_value=result.p_value,
                    adjusted_p=result.adjusted_p,
                    sample_size=points,
                    correction="Benjamini-Hochberg FDR",
                    notes=[
                        f"{len(candidates)} products tested together.",
                        "Association, not cause: a season or a promotion moves "
                        "price and volume at the same time.",
                    ],
                ),
                severity=severity,
                importance=0.72 if not revenue_follows_price else 0.55,
                chart_data={
                    "points": [
                        {"x": float(p), "y": float(u)}
                        for p, u in zip(
                            grouped.loc[product]["price"].dropna().tolist(),
                            grouped.loc[product]["units"].dropna().tolist(),
                        )
                    ],
                    "x_label": "average price",
                    "y_label": "units sold",
                },
            )
        )

    # One is a finding; twelve is a table. Keep the strongest responses.
    findings.sort(key=lambda f: -abs(float(f.facts["elasticity"])))
    return findings[:2]


# --------------------------------------------------------------------------
# What is new, what is fading, what has stopped
# --------------------------------------------------------------------------


def lifecycle(frame: SalesFrame) -> list[Finding]:
    """Products that are new, fading, or have quietly stopped selling.

    A product that stops selling is invisible to every ranking in the product,
    because a ranking can only show what is present. It leaves no gap. The only
    way to notice is to ask which products used to appear and no longer do, so
    that is asked here.
    """
    if not frame.has(PRODUCT) or not frame.has(REVENUE):
        return []

    freq = frame.natural_frequency()
    pivot = frame.data.pivot_table(
        index=pd.Grouper(key=DATE, freq=freq),
        columns=PRODUCT,
        values=REVENUE,
        aggfunc="sum",
    ).fillna(0.0)
    if len(pivot) < 4 or pivot.shape[1] < 2:
        return []

    periods = list(pivot.index)
    recent = periods[-DORMANT_PERIODS:]
    earlier = periods[:-DORMANT_PERIODS]
    if not earlier:
        return []

    stopped: list[dict[str, object]] = []
    arrived: list[dict[str, object]] = []
    for product in pivot.columns:
        column = pivot[product]
        traded_before = (column.loc[earlier] > 0).sum()
        traded_recently = (column.loc[recent] > 0).sum()

        # Was a regular, then nothing. A product that only ever appeared once
        # or twice is not a loss, it was never established.
        if traded_recently == 0 and traded_before >= max(3, len(earlier) // 3):
            last = column[column > 0].index[-1]
            stopped.append(
                {
                    "product": str(product),
                    "was_worth": float(column.loc[earlier].mean()),
                    "last_seen": str(pd.Timestamp(last).date()),
                }
            )
        # Never appeared before, selling now.
        if traded_before == 0 and traded_recently > 0:
            arrived.append(
                {
                    "product": str(product),
                    "now_worth": float(column.loc[recent].mean()),
                }
            )

    findings: list[Finding] = []
    period_word = {"MS": "months", "W": "weeks", "D": "days"}.get(freq, "periods")

    if stopped:
        stopped.sort(key=lambda s: -float(s["was_worth"]))
        biggest = stopped[0]
        names = ", ".join(str(s["product"]) for s in stopped[:3])
        summary = (
            f"{len(stopped)} product{'s' if len(stopped) > 1 else ''} stopped "
            f"selling and did not come back: {names}"
            f"{' and others' if len(stopped) > 3 else ''}. "
            f"{biggest['product']} was worth about "
            f"{_money(float(biggest['was_worth']))} in an average "
            f"{period_word[:-1]} before it went quiet."
        )
        findings.append(
            Finding(
                id="products_stopped",
                type=FindingType.LIFECYCLE,
                summary=summary,
                facts={
                    "stopped": stopped[:10],
                    "count": len(stopped),
                    "quiet_periods": DORMANT_PERIODS,
                    "period": period_word,
                },
                evidence=Evidence(
                    method=(
                        f"products that traded regularly and then recorded "
                        f"nothing for the last {DORMANT_PERIODS} {period_word}"
                    ),
                    sample_size=int(pivot.shape[1]),
                    notes=[
                        "A stopped product leaves no row, so no ranking can "
                        "show it. This looks for the absence.",
                        "Could be a delisting, a stockout, or a supplier gone; "
                        "the data cannot tell which.",
                    ],
                ),
                severity=Severity.WATCH,
                importance=0.68,
                chart_data={
                    "bars": [
                        {"label": str(s["product"]), "value": float(s["was_worth"])}
                        for s in stopped[:8]
                    ]
                },
            )
        )

    if arrived:
        arrived.sort(key=lambda a: -float(a["now_worth"]))
        best = arrived[0]
        summary = (
            f"{len(arrived)} product{'s' if len(arrived) > 1 else ''} appeared "
            f"for the first time in the last {DORMANT_PERIODS} {period_word}. "
            f"{best['product']} is already worth about "
            f"{_money(float(best['now_worth']))} in an average "
            f"{period_word[:-1]}."
        )
        findings.append(
            Finding(
                id="products_arrived",
                type=FindingType.LIFECYCLE,
                summary=summary,
                facts={"arrived": arrived[:10], "count": len(arrived)},
                evidence=Evidence(
                    method=(
                        f"products with no sales before the last "
                        f"{DORMANT_PERIODS} {period_word}"
                    ),
                    sample_size=int(pivot.shape[1]),
                    notes=[
                        "Too new to judge: a first strong period is not a trend."
                    ],
                ),
                severity=Severity.GOOD,
                importance=0.45,
                chart_data={
                    "bars": [
                        {"label": str(a["product"]), "value": float(a["now_worth"])}
                        for a in arrived[:8]
                    ]
                },
            )
        )

    return findings


# --------------------------------------------------------------------------
# The shape the average hides
# --------------------------------------------------------------------------


def order_spread(frame: SalesFrame) -> list[Finding]:
    """What a typical order is really worth, and how lopsided the mix is.

    Every other comparison in the engine leans on the mean, and a mean is a poor
    summary of money. Order values are almost always skewed: a handful of large
    orders drag the average above anything most customers actually spend, so
    "the average order is 40,000" can be true while most orders are nearer
    12,000. That gap changes what a business should expect from a typical sale,
    and no ranking reveals it.
    """
    order_column = "order_id" if frame.has("order_id") else None
    if order_column:
        values = frame.data.groupby(order_column)[REVENUE].sum()
        unit = "order"
    else:
        # No order id: each row is a line, which is still worth describing, and
        # the wording says which it is rather than implying orders.
        values = frame.data[REVENUE].dropna()
        unit = "sale"

    values = values[values > 0]
    if len(values) < 20:
        return []

    mean = float(values.mean())
    median = float(values.median())
    if median <= 0:
        return []

    top_decile_cut = float(values.quantile(0.9))
    # The largest tenth of the orders, not every order at or above the ninetieth
    # percentile. Those differ badly when values repeat: on a file where every
    # order is worth the same, the threshold comparison selects all of them and
    # the finding claims the top ten percent bring in all the money.
    top_n = max(1, int(np.ceil(len(values) * 0.1)))
    top_decile_share = float(values.nlargest(top_n).sum() / values.sum())
    skew = mean / median

    facts = {
        "unit": unit,
        "count": int(len(values)),
        "mean": mean,
        "median": median,
        "p10": float(values.quantile(0.1)),
        "p90": top_decile_cut,
        "top_decile_share": top_decile_share,
        "mean_to_median": skew,
    }

    # Only worth a card when the average is actually misleading.
    if skew < 1.25 and top_decile_share < 0.35:
        return []

    summary = (
        f"Half your {unit}s are under {_money(median)}, while the average is "
        f"{_money(mean)}. The largest one in ten bring in "
        f"{top_decile_share:.0%} of the money, which is what pulls the average "
        f"above what most {unit}s are actually worth."
    )

    return [
        Finding(
            id="order_spread",
            type=FindingType.DISTRIBUTION,
            summary=summary,
            facts=facts,
            evidence=Evidence(
                method=f"distribution of {unit} values, {len(values):,} of them",
                sample_size=int(len(values)),
                notes=[
                    "Descriptive. The median and the average are both correct; "
                    "they answer different questions."
                ],
            ),
            severity=Severity.NEUTRAL,
            importance=0.5,
            chart_data={
                "bars": [
                    {"label": "cheapest 10%", "value": float(values.quantile(0.1))},
                    {"label": "typical (middle)", "value": median},
                    {"label": "average", "value": mean},
                    {"label": "dearest 10% start", "value": top_decile_cut},
                ]
            },
            tier=Tier.CORE,
        )
    ]
