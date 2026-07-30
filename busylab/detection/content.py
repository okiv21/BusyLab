"""Layer 2: content and type inference.

This layer inspects the data, not the name (spec 3.2). It does two jobs that
the keyword dictionary structurally cannot:

* **Rescue.** A column called ``Column3`` that parses cleanly as dates is the
  date column, whatever it is called.
* **Veto.** A column called ``date`` holding ``"Lagos, Ikeja"`` is not the date
  column, however confident the name was. This is the check that stops
  ``discount_amount`` being silently used as revenue.

Content can only ever say how *plausible* a role is given the values. Several
roles are genuinely indistinguishable by content alone: channel, region,
category and payment method are all just low-cardinality text, and cost and
unit price are both just positive money. Those ties are meant to be broken by
the keyword layer or, failing that, by the user. Pretending otherwise would be
inventing certainty.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from pandas.errors import OutOfBoundsDatetime

from .. import cleaning
from ..roles import Role


def _trapezoid(x: float, rise_lo: float, rise_hi: float, fall_lo: float, fall_hi: float) -> float:
    """Membership score in ``[0, 1]`` for a soft range.

    Zero below ``rise_lo`` and above ``fall_hi``, one between ``rise_hi`` and
    ``fall_lo``, linear in between. Used so "a few distinct values" degrades
    gracefully instead of snapping at an arbitrary cutoff.
    """
    if x <= rise_lo or x >= fall_hi:
        return 0.0
    if x < rise_hi:
        return (x - rise_lo) / (rise_hi - rise_lo) if rise_hi > rise_lo else 1.0
    if x <= fall_lo:
        return 1.0
    return (fall_hi - x) / (fall_hi - fall_lo) if fall_hi > fall_lo else 1.0


@dataclass
class ContentProfile:
    """What the values in a column actually look like."""

    name: str
    n_total: int = 0
    n_present: int = 0
    blank_rate: float = 1.0

    numeric_rate: float = 0.0
    datetime_rate: float = 0.0
    was_percent: bool = False
    was_excel_serial: bool = False
    dayfirst: bool | None = None

    n_unique: int = 0
    uniqueness: float = 0.0
    top_value_share: float = 0.0

    # Numeric shape, only meaningful when numeric_rate is high.
    integer_rate: float = 0.0
    positive_rate: float = 0.0
    zero_rate: float = 0.0
    negative_rate: float = 0.0
    median_abs: float = 0.0
    max_abs: float = 0.0
    numeric_n_unique: int = 0

    # Text shape.
    avg_text_len: float = 0.0
    max_text_len: int = 0

    # Temporal shape, only meaningful when datetime_rate is high.
    date_min: pd.Timestamp | None = None
    date_max: pd.Timestamp | None = None
    date_span_days: float = 0.0

    notes: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.n_present == 0

    @property
    def is_constant(self) -> bool:
        return self.n_present > 0 and self.n_unique <= 1


def profile_column(series: pd.Series, name: str | None = None) -> ContentProfile:
    """Measure a single column. Cheap enough to run on every column."""
    label = str(name if name is not None else series.name)
    profile = ContentProfile(name=label, n_total=int(len(series)))

    present = cleaning.non_null(series)
    profile.n_present = int(len(present))
    profile.blank_rate = (
        1.0 - profile.n_present / profile.n_total if profile.n_total else 1.0
    )
    if profile.n_present == 0:
        profile.notes.append("column is empty")
        return profile

    # Cardinality on the raw presented values.
    text = cleaning.to_text(series)
    profile.n_unique = int(text.nunique())
    profile.uniqueness = profile.n_unique / profile.n_present
    counts = text.value_counts()
    profile.top_value_share = float(counts.iloc[0] / profile.n_present)
    lengths = text.str.len()
    profile.avg_text_len = float(lengths.mean())
    profile.max_text_len = int(lengths.max())

    numeric = cleaning.to_numeric(series)
    profile.numeric_rate = numeric.rate
    profile.was_percent = numeric.was_percent

    temporal = cleaning.to_datetime(series)
    profile.datetime_rate = temporal.rate
    profile.was_excel_serial = temporal.was_excel_serial
    profile.dayfirst = temporal.dayfirst

    if profile.numeric_rate > 0:
        vals = numeric.values.dropna()
        if len(vals):
            profile.integer_rate = float(np.isclose(vals % 1, 0).mean())
            profile.positive_rate = float((vals > 0).mean())
            profile.zero_rate = float((vals == 0).mean())
            profile.negative_rate = float((vals < 0).mean())
            nonzero = vals[vals != 0].abs()
            profile.median_abs = float(nonzero.median()) if len(nonzero) else 0.0
            profile.max_abs = float(vals.abs().max())
            profile.numeric_n_unique = int(vals.nunique())

    if profile.datetime_rate > 0:
        stamps = temporal.values.dropna()
        if len(stamps):
            profile.date_min = stamps.min()
            profile.date_max = stamps.max()
            try:
                profile.date_span_days = float(
                    (profile.date_max - profile.date_min).days
                )
            except (OverflowError, OutOfBoundsDatetime, ValueError):
                # Nonsense dates spanning centuries mean this is not a date
                # column at all, whatever the parser managed to produce.
                profile.date_span_days = 0.0
                profile.datetime_rate = 0.0
                profile.notes.append("date values span an implausible range")

    return profile


# --------------------------------------------------------------------------
# Role affinity from content alone
# --------------------------------------------------------------------------

#: Below this, a column simply is not the thing, whatever it is called.
VETO_THRESHOLD = 0.15


def _score_date(p: ContentProfile) -> float:
    if p.datetime_rate < 0.5:
        return 0.0
    score = p.datetime_rate
    # A real date column spans time. A single repeated date is a report
    # header artefact, not a transaction date.
    if p.date_span_days <= 0:
        score *= 0.4
    return min(score, 1.0)


def _categorical_base(p: ContentProfile) -> float:
    """Common shape of any text label column: repeated, short-ish, not numeric."""
    if p.numeric_rate >= 0.95 or p.datetime_rate >= 0.9:
        return 0.0
    if p.n_present < 2 or p.is_constant:
        return 0.0
    return 1.0


def _score_product(p: ContentProfile) -> float:
    base = _categorical_base(p)
    if base == 0.0:
        return 0.0
    # Many distinct values, each recurring. Too few and it is a channel;
    # near-unique and it is an identifier.
    spread = _trapezoid(p.n_unique, 1, 3, 400, 5000)
    repetition = _trapezoid(p.uniqueness, 0.0, 0.002, 0.35, 0.95)
    length = _trapezoid(p.avg_text_len, 0.5, 2.0, 60.0, 200.0)
    return base * spread * repetition * length


def _score_low_card_label(p: ContentProfile) -> float:
    """Channel, region, category, payment method: a handful of repeated labels."""
    base = _categorical_base(p)
    if base == 0.0:
        return 0.0
    spread = _trapezoid(p.n_unique, 1, 2, 12, 60)
    repetition = _trapezoid(p.uniqueness, 0.0, 0.0005, 0.12, 0.6)
    length = _trapezoid(p.avg_text_len, 0.5, 1.5, 30.0, 120.0)
    return base * spread * repetition * length


def _identifier_penalty(p: ContentProfile) -> float:
    """Identifiers are codes, not measurements.

    A numeric column carrying decimals is a price or a discount, never a
    customer or order number, so fractional values discount the identifier
    reading sharply rather than ruling it out outright.
    """
    if p.numeric_rate < 0.9:
        return 1.0  # text codes are perfectly good identifiers
    if p.was_percent:
        return 0.05
    return 1.0 if p.integer_rate >= 0.98 else 0.1


def _score_customer_id(p: ContentProfile) -> float:
    if p.datetime_rate >= 0.9 or p.is_constant or p.n_present < 2:
        return 0.0
    # Repeat customers are the point, so uniqueness sits below 1 but well
    # above a channel column.
    repetition = _trapezoid(p.uniqueness, 0.02, 0.12, 0.92, 1.0)
    spread = _trapezoid(p.n_unique, 2, 8, 100000, 500000)
    return repetition * spread * _identifier_penalty(p)


def _score_order_id(p: ContentProfile) -> float:
    if p.datetime_rate >= 0.9 or p.is_constant or p.n_present < 2:
        return 0.0
    # One row per order, or a few lines per order.
    return _trapezoid(p.uniqueness, 0.15, 0.45, 1.0, 1.01) * _identifier_penalty(p)


def _score_quantity(p: ContentProfile) -> float:
    if p.numeric_rate < 0.9 or p.was_percent:
        return 0.0
    if p.positive_rate < 0.5:
        return 0.0
    # Whole units, small, and only a handful of distinct values: 1, 2, 3...
    wholeness = p.integer_rate
    smallness = _trapezoid(p.median_abs, 0.0, 0.9, 40.0, 5000.0)
    coarseness = _trapezoid(p.numeric_n_unique, 0, 1, 40, 800)
    return wholeness * max(smallness, 0.15) * max(coarseness, 0.2)


def _score_money(p: ContentProfile) -> float:
    """Generic 'this is an amount of money' shape.

    Revenue, unit price and cost are all this shape. Content cannot separate
    them; the name must. Returning the same score for all three is honest and
    lets the keyword layer or the user decide.
    """
    if p.numeric_rate < 0.9 or p.was_percent:
        return 0.0
    if p.positive_rate < 0.4:
        return 0.0
    magnitude = _trapezoid(p.median_abs, 0.0, 0.5, 5e8, 5e10)
    # Money usually takes many distinct values; a column of only 1s and 2s is
    # a count, not a price.
    granularity = _trapezoid(p.numeric_n_unique, 1, 5, 1e6, 1e7)
    score = magnitude * max(granularity, 0.25)

    # Small whole numbers drawn from a short list are units sold. Damped
    # rather than zeroed, because a cheap item can genuinely cost 3 naira.
    count_like = (
        p.integer_rate >= 0.98 and p.median_abs <= 20 and p.numeric_n_unique <= 25
    )
    if count_like:
        score *= 0.35
    return score


def _score_discount(p: ContentProfile) -> float:
    if p.numeric_rate < 0.9:
        return 0.0
    # Discounts are mostly zero, or expressed as a rate.
    if p.was_percent:
        return 0.95
    if p.zero_rate >= 0.25:
        return 0.7 + 0.25 * min(p.zero_rate, 1.0)
    if p.positive_rate > 0.4 and p.max_abs <= 1.0 and p.median_abs < 1.0:
        return 0.8  # a 0..1 rate stored without a % sign
    return 0.25 * _score_money(p)


def score_content(profile: ContentProfile) -> dict[Role, float]:
    """How plausible each role is, judged only on the values.

    Scores are independent of one another; two roles can both score 1.0 when
    the content genuinely cannot tell them apart.
    """
    if profile.is_empty:
        return {}

    label = _score_low_card_label(profile)
    money = _score_money(profile)

    scores: dict[Role, float] = {
        Role.DATE: _score_date(profile),
        Role.PRODUCT: _score_product(profile),
        Role.CUSTOMER_ID: _score_customer_id(profile),
        Role.ORDER_ID: _score_order_id(profile),
        Role.QUANTITY: _score_quantity(profile),
        Role.REVENUE: money,
        Role.UNIT_PRICE: money,
        Role.COST: money,
        Role.DISCOUNT: _score_discount(profile),
        Role.CHANNEL: label,
        Role.REGION: label,
        Role.CATEGORY: label,
        Role.PAYMENT_METHOD: label,
    }
    return {role: round(float(v), 4) for role, v in scores.items() if v > 0}


def supports(profile: ContentProfile, role: Role) -> bool:
    """Does the content permit this role at all?

    A False here overrides any keyword match, which is the veto that keeps a
    confidently-named column from being silently wrong.
    """
    return score_content(profile).get(role, 0.0) >= VETO_THRESHOLD
