"""The canonical table every analysis reads from.

Detection says which column means what. This module turns that into one typed,
cleaned frame with known column names, so no analysis ever has to think about
currency symbols, cost bases or missing revenue again.

Deriving revenue and profit happens exactly once, here. In particular the cost
basis matters: if a per-unit cost is treated as a line total then every margin
in the product is wrong, so the basis detection carries through into the
arithmetic rather than being re-guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import cleaning
from ..detection.engine import DetectionResult
from ..roles import Role, Tier

#: Canonical column names inside a SalesFrame.
DATE = "date"
PRODUCT = "product"
REVENUE = "revenue"
QUANTITY = "quantity"
UNIT_PRICE = "unit_price"
COST = "cost"
PROFIT = "profit"
MARGIN = "margin"
CUSTOMER = "customer_id"
ORDER = "order_id"
DISCOUNT = "discount"

#: Roles that become an optional grouping dimension if present.
GROUPING_ROLES = (Role.CHANNEL, Role.REGION, Role.CATEGORY, Role.PAYMENT_METHOD)


@dataclass
class SalesFrame:
    """Cleaned, typed sales data plus what is known about it."""

    data: pd.DataFrame
    tiers: dict[Tier, bool] = field(default_factory=dict)
    #: Extra categorical columns available to group by, canonical name -> label.
    dimensions: dict[str, str] = field(default_factory=dict)
    cost_basis: str | None = None
    rows_dropped: int = 0
    notes: list[str] = field(default_factory=list)

    # -- availability -----------------------------------------------------
    def has(self, column: str) -> bool:
        return column in self.data.columns and self.data[column].notna().any()

    @property
    def has_profit(self) -> bool:
        return self.has(PROFIT)

    @property
    def has_customers(self) -> bool:
        return self.has(CUSTOMER)

    @property
    def n_rows(self) -> int:
        return int(len(self.data))

    @property
    def date_min(self) -> pd.Timestamp:
        return self.data[DATE].min()

    @property
    def date_max(self) -> pd.Timestamp:
        return self.data[DATE].max()

    @property
    def span_days(self) -> int:
        return int((self.date_max - self.date_min).days)

    @property
    def products(self) -> list[str]:
        return sorted(self.data[PRODUCT].dropna().unique().tolist())

    # -- shaping ----------------------------------------------------------
    def by_period(
        self,
        freq: str = "MS",
        value: str = REVENUE,
        how: str = "sum",
        *,
        drop_partial: bool = False,
    ) -> pd.Series:
        """Total ``value`` per period. ``MS`` is calendar months.

        ``drop_partial`` removes a trailing period the data does not fully
        cover. A file exported on the 23rd has a final month holding two thirds
        of a month's sales, which looks exactly like a collapse — and anything
        fitted through it will then "recover" from a crash that never happened.
        """
        if value not in self.data.columns:
            return pd.Series(dtype="float64")
        grouped = self.data.set_index(DATE)[value].resample(freq)
        series = getattr(grouped, how)()
        series = series.dropna() if how != "sum" else series
        if drop_partial and len(series) > 1 and self._last_period_incomplete(freq):
            series = series.iloc[:-1]
        return series

    #: Resample offsets and Period frequencies are different vocabularies:
    #: "MS" is a valid resample rule but not a valid Period freq, and asking
    #: for one with the other raises rather than converting.
    _PERIOD_FREQ = {"MS": "M", "M": "M", "QS": "Q", "Q": "Q", "YS": "Y", "A": "Y", "W": "W", "D": "D"}

    def _last_period_incomplete(self, freq: str) -> bool:
        """True when the data stops before the end of its final period."""
        if self.data.empty:
            return False
        period_freq = self._PERIOD_FREQ.get(freq, freq)
        last = self.data[DATE].max()
        try:
            period_end = last.to_period(period_freq).end_time
        except (ValueError, AttributeError):
            return False
        # Same-day tolerance: a period is complete once its final day appears.
        return bool(last.normalize() < period_end.normalize())

    def by_product(self, value: str = REVENUE, how: str = "sum") -> pd.Series:
        """Total ``value`` per product, largest first."""
        if value not in self.data.columns:
            return pd.Series(dtype="float64")
        series = getattr(self.data.groupby(PRODUCT)[value], how)()
        return series.sort_values(ascending=False)

    def product_period(
        self, freq: str = "MS", value: str = REVENUE, *, drop_partial: bool = False
    ) -> pd.DataFrame:
        """A product-by-period matrix, for trends and correlations."""
        if value not in self.data.columns:
            return pd.DataFrame()
        pivot = self.data.pivot_table(
            index=pd.Grouper(key=DATE, freq=freq),
            columns=PRODUCT,
            values=value,
            aggfunc="sum",
        )
        pivot = pivot.fillna(0.0)
        if drop_partial and len(pivot) > 1 and self._last_period_incomplete(freq):
            pivot = pivot.iloc[:-1]
        return pivot

    def natural_frequency(self) -> str:
        """Month, week or day, whichever gives enough points to reason about.

        Statistics on six monthly points is a guess dressed as a test, so the
        period shrinks when the history is short rather than pretending.
        """
        span = self.span_days
        if span >= 730:
            return "MS"
        if span >= 180:
            return "MS"
        if span >= 60:
            return "W"
        return "D"


def build(
    raw: pd.DataFrame, detection: DetectionResult, *, min_rows: int = 12
) -> SalesFrame:
    """Assemble a :class:`SalesFrame` from raw data and a detection result.

    Rows missing a date, a product or a value cannot be analysed and are
    dropped, with the count reported rather than hidden.
    """
    columns = detection.assignments
    if Role.DATE not in columns or Role.PRODUCT not in columns:
        raise ValueError("A date column and a product column are required.")

    frame = pd.DataFrame(index=raw.index)

    frame[DATE] = cleaning.to_datetime(raw[columns[Role.DATE]]).values.reindex(raw.index)
    frame[PRODUCT] = (
        cleaning.to_text(raw[columns[Role.PRODUCT]]).reindex(raw.index).astype("object")
    )

    def numeric(role: Role) -> pd.Series | None:
        column = columns.get(role)
        if column is None or column not in raw.columns:
            return None
        return cleaning.to_numeric(raw[column]).values.reindex(raw.index)

    quantity = numeric(Role.QUANTITY)
    unit_price = numeric(Role.UNIT_PRICE)
    revenue = numeric(Role.REVENUE)
    cost = numeric(Role.COST)
    discount = numeric(Role.DISCOUNT)

    notes: list[str] = []

    if revenue is None:
        if quantity is None or unit_price is None:
            raise ValueError(
                "A revenue column, or quantity and unit price together, is required."
            )
        revenue = quantity * unit_price
        notes.append("Revenue computed from quantity x unit price.")

    frame[REVENUE] = revenue
    if quantity is not None:
        frame[QUANTITY] = quantity
    if unit_price is not None:
        frame[UNIT_PRICE] = unit_price
    elif quantity is not None:
        # Useful for price-vs-volume decomposition even when not supplied.
        with np.errstate(divide="ignore", invalid="ignore"):
            frame[UNIT_PRICE] = frame[REVENUE] / quantity.replace(0, np.nan)
    if discount is not None:
        frame[DISCOUNT] = discount

    # Profit, computed once, using the detected cost basis.
    basis = detection.cost_basis
    if cost is not None:
        if basis == "per_unit" and quantity is not None:
            line_cost = cost * quantity
            notes.append("Profit uses cost x quantity, cost being per unit.")
        else:
            line_cost = cost
            if basis == "per_unit":
                notes.append(
                    "Cost looks per-unit but there is no quantity column, so it "
                    "is treated as a line total."
                )
            else:
                notes.append("Profit uses cost as a line total.")
        frame[COST] = line_cost
        frame[PROFIT] = frame[REVENUE] - line_cost
        with np.errstate(divide="ignore", invalid="ignore"):
            frame[MARGIN] = np.where(
                frame[REVENUE] > 0, frame[PROFIT] / frame[REVENUE], np.nan
            )

    for role, name in ((Role.CUSTOMER_ID, CUSTOMER), (Role.ORDER_ID, ORDER)):
        column = columns.get(role)
        if column is not None and column in raw.columns:
            frame[name] = cleaning.to_text(raw[column]).reindex(raw.index)

    dimensions: dict[str, str] = {}
    for role in GROUPING_ROLES:
        column = columns.get(role)
        if column is None or column not in raw.columns:
            continue
        canonical = role.value
        frame[canonical] = cleaning.to_text(raw[column]).reindex(raw.index)
        dimensions[canonical] = role.value.replace("_", " ")

    # User-tagged generic groupings (spec 3.3's middle path).
    for verdict in detection.verdicts:
        if verdict.role is Role.GROUP_BY and verdict.column in raw.columns:
            canonical = f"group_{verdict.column}"
            frame[canonical] = cleaning.to_text(raw[verdict.column]).reindex(raw.index)
            dimensions[canonical] = verdict.column

    before = len(frame)
    frame = frame.dropna(subset=[DATE, PRODUCT, REVENUE])
    frame = frame[frame[PRODUCT].astype(str).str.strip() != ""]
    dropped = before - len(frame)
    if dropped:
        notes.append(f"{dropped} rows without a date, product or value were skipped.")

    frame = frame.sort_values(DATE).reset_index(drop=True)

    if len(frame) < min_rows:
        notes.append(
            f"Only {len(frame)} usable rows; findings will be limited."
        )

    return SalesFrame(
        data=frame,
        tiers=dict(detection.tiers),
        dimensions=dimensions,
        cost_basis=basis,
        rows_dropped=dropped,
        notes=notes + list(detection.notes),
    )
