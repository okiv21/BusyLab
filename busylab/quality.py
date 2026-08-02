"""The data quality gate.

Automated ingestion means bad data arrives automatically too, and nobody is
watching (spec 4.3). A refresh with half-filled rows, a duplicated month or a
broken export will silently poison the analysis, and the output of a poisoned
analysis looks exactly as confident as the output of a good one. That is the
whole danger.

So every ingest passes this gate before it is allowed to publish findings. A
refresh that fails **holds the analysis and raises a flag** rather than
publishing something confidently wrong. Spec 4.3 is explicit that this is not
optional.

The gate compares against a snapshot of the previous good run where one
exists, which is what makes "the row count halved" and "the values shifted"
answerable at all. On a first upload those checks simply do not run, rather
than guessing at a baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from . import cleaning
from .roles import Role

#: A period more than this far below its neighbours is probably a partial
#: export rather than a bad month.
PARTIAL_PERIOD_RATIO = 0.35
#: Null rate in a required column above this makes the analysis untrustworthy.
NULL_SPIKE_BLOCK = 0.30
NULL_SPIKE_WARN = 0.10
#: Share of rows that may be exact duplicates before it is a real problem.
DUPLICATE_BLOCK = 0.10
DUPLICATE_WARN = 0.01
#: Row count outside this band versus the last good run is suspicious.
ROW_DROP_BLOCK = 0.50
ROW_DROP_WARN = 0.25
#: Median value moving by more than this versus history is a distribution shift.
SHIFT_BLOCK = 3.0
SHIFT_WARN = 1.6


class Severity(str, Enum):
    """How much a problem matters.

    ``BLOCK`` holds the analysis. ``WARN`` publishes with a caveat, because a
    gate that stops everything at the first imperfection is a gate nobody
    leaves switched on.
    """

    BLOCK = "block"
    WARN = "warn"
    INFO = "info"


@dataclass
class QualityIssue:
    """One thing wrong with an incoming refresh."""

    code: str
    severity: Severity
    title: str
    detail: str
    #: How many rows or periods are affected, where that makes sense.
    count: int | None = None
    #: A few examples, so the user can go and look.
    sample: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "title": self.title,
            "detail": self.detail,
            "count": self.count,
            "sample": self.sample,
        }


@dataclass
class Snapshot:
    """What a good run looked like, so the next one has something to compare to."""

    rows: int = 0
    median_value: float = 0.0
    products: int = 0
    date_min: str = ""
    date_max: str = ""
    taken_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "median_value": self.median_value,
            "products": self.products,
            "date_min": self.date_min,
            "date_max": self.date_max,
            "taken_at": self.taken_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "Snapshot | None":
        if not raw:
            return None
        return cls(
            rows=int(raw.get("rows", 0)),
            median_value=float(raw.get("median_value", 0.0)),
            products=int(raw.get("products", 0)),
            date_min=str(raw.get("date_min", "")),
            date_max=str(raw.get("date_max", "")),
            taken_at=str(raw.get("taken_at", "")),
        )


@dataclass
class QualityReport:
    """The gate's verdict on one refresh."""

    issues: list[QualityIssue] = field(default_factory=list)
    snapshot: Snapshot | None = None
    compared_against: Snapshot | None = None

    @property
    def blocking(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity is Severity.BLOCK]

    @property
    def warnings(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity is Severity.WARN]

    @property
    def passed(self) -> bool:
        """True when findings may be published."""
        return not self.blocking

    @property
    def headline(self) -> str:
        if self.passed and not self.warnings:
            return "This data looks healthy."
        if self.passed:
            return f"{len(self.warnings)} thing(s) worth knowing about this data."
        return f"Analysis held: {self.blocking[0].title}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "headline": self.headline,
            "issues": [i.to_dict() for i in self.issues],
            "snapshot": self.snapshot.to_dict() if self.snapshot else None,
        }


def _samples(values, limit: int = 3) -> list[str]:
    return [str(v) for v in list(values)[:limit]]


def check(
    raw: pd.DataFrame,
    assignments: dict[Role, str],
    *,
    previous: Snapshot | dict[str, Any] | None = None,
) -> QualityReport:
    """Run every gate check over an incoming frame.

    ``assignments`` comes from detection, so the gate knows which column is the
    date and which is the value. ``previous`` is the snapshot from the last run
    that passed; without one, the history-dependent checks are skipped rather
    than guessed.
    """
    if isinstance(previous, dict):
        previous = Snapshot.from_dict(previous)

    report = QualityReport(compared_against=previous)
    issues = report.issues

    date_col = assignments.get(Role.DATE)
    product_col = assignments.get(Role.PRODUCT)
    value_col = assignments.get(Role.REVENUE)

    if raw.empty:
        issues.append(
            QualityIssue(
                "empty_file",
                Severity.BLOCK,
                "This file has no rows",
                "There is nothing to analyse.",
            )
        )
        return report

    dates = (
        cleaning.to_datetime(raw[date_col]).values.reindex(raw.index)
        if date_col in raw.columns
        else None
    )
    values = (
        cleaning.to_numeric(raw[value_col]).values.reindex(raw.index)
        if value_col and value_col in raw.columns
        else None
    )

    _check_duplicates(raw, issues)
    _check_nulls(raw, assignments, issues)
    if dates is not None:
        _check_dates(dates, issues)
        _check_period_gaps(dates, issues)
        _check_duplicated_period(dates, values, issues)
    if values is not None:
        _check_values(values, issues)

    if previous is not None:
        _check_against_history(raw, dates, values, previous, issues)

    report.snapshot = _snapshot(raw, dates, values, product_col)
    return report


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------


def _check_duplicates(raw: pd.DataFrame, issues: list[QualityIssue]) -> None:
    """Exact duplicate rows, which double-count silently."""
    duplicated = raw.duplicated()
    count = int(duplicated.sum())
    if count == 0:
        return
    share = count / len(raw)
    severity = (
        Severity.BLOCK
        if share >= DUPLICATE_BLOCK
        else Severity.WARN
        if share >= DUPLICATE_WARN
        else Severity.INFO
    )
    issues.append(
        QualityIssue(
            "duplicate_rows",
            severity,
            f"{count:,} rows appear more than once",
            (
                f"{share:.0%} of the file is exact duplicates. Duplicated rows "
                "inflate every total they touch."
            ),
            count=count,
            sample=_samples(raw.index[duplicated]),
        )
    )


def _check_nulls(
    raw: pd.DataFrame, assignments: dict[Role, str], issues: list[QualityIssue]
) -> None:
    """Missing values in the columns the analysis cannot work without."""
    for role in (Role.DATE, Role.PRODUCT, Role.REVENUE):
        column = assignments.get(role)
        if column is None or column not in raw.columns:
            continue
        blank = cleaning.blank_mask(raw[column])
        rate = float(blank.mean())
        if rate < NULL_SPIKE_WARN:
            continue
        severity = Severity.BLOCK if rate >= NULL_SPIKE_BLOCK else Severity.WARN
        issues.append(
            QualityIssue(
                "null_spike",
                severity,
                f"{rate:.0%} of {role.value} values are missing",
                (
                    f'Column "{column}" is empty on {int(blank.sum()):,} rows. '
                    "Those rows cannot be counted."
                ),
                count=int(blank.sum()),
            )
        )


def _check_dates(dates: pd.Series, issues: list[QualityIssue]) -> None:
    """Dates that cannot be right: the future, or the distant past."""
    parsed = dates.dropna()
    if parsed.empty:
        issues.append(
            QualityIssue(
                "no_dates",
                Severity.BLOCK,
                "No usable dates",
                "Nothing in the date column could be read as a date.",
            )
        )
        return

    now = pd.Timestamp.now()
    future = parsed[parsed > now + pd.Timedelta(days=1)]
    if len(future):
        share = len(future) / len(parsed)
        issues.append(
            QualityIssue(
                "future_dates",
                Severity.BLOCK if share > 0.05 else Severity.WARN,
                f"{len(future):,} rows are dated in the future",
                "Future-dated rows usually mean a day/month mix-up in the export.",
                count=int(len(future)),
                sample=_samples(future.dt.date.unique()),
            )
        )

    ancient = parsed[parsed < pd.Timestamp("1990-01-01")]
    if len(ancient):
        issues.append(
            QualityIssue(
                "ancient_dates",
                Severity.WARN,
                f"{len(ancient):,} rows are dated before 1990",
                "These are usually a failed date conversion rather than real sales.",
                count=int(len(ancient)),
                sample=_samples(ancient.dt.date.unique()),
            )
        )


def _check_period_gaps(dates: pd.Series, issues: list[QualityIssue]) -> None:
    """Whole months missing from the middle of the history.

    A gap at the end is just "the month is not over". A gap in the middle means
    something did not export, and every trend drawn through it is wrong.
    """
    parsed = dates.dropna()
    if len(parsed) < 60:
        return
    months = parsed.dt.to_period("M")
    present = set(months.unique())
    span = pd.period_range(months.min(), months.max(), freq="M")
    if len(span) < 4:
        return
    # Ignore the final period: it is usually simply still in progress.
    missing = [str(p) for p in span[:-1] if p not in present]
    if not missing:
        return
    issues.append(
        QualityIssue(
            "date_gaps",
            Severity.BLOCK if len(missing) > 1 else Severity.WARN,
            f"{len(missing)} month(s) missing from the middle of the history",
            (
                "A trend drawn across a missing month is measuring the gap, "
                "not the business."
            ),
            count=len(missing),
            sample=missing[:4],
        )
    )


def _check_duplicated_period(
    dates: pd.Series, values: pd.Series | None, issues: list[QualityIssue]
) -> None:
    """A month appended twice, which is the classic broken re-import.

    Detected as a period whose row count is close to double the median of its
    neighbours, rather than by looking for duplicate rows, because a re-import
    often carries new ids and so is not row-identical.
    """
    parsed = dates.dropna()
    if len(parsed) < 90:
        return
    per_month = parsed.dt.to_period("M").value_counts().sort_index()
    if len(per_month) < 4:
        return
    counts = per_month.to_numpy(dtype=float)
    median = float(np.median(counts))
    if median <= 0:
        return
    suspect = per_month[counts > median * 1.85]
    if suspect.empty:
        return
    issues.append(
        QualityIssue(
            "duplicated_period",
            Severity.WARN,
            f"{len(suspect)} month(s) hold about twice the usual number of rows",
            (
                "This is the shape a month imported twice makes. Worth checking "
                "before trusting the totals for those months."
            ),
            count=int(len(suspect)),
            sample=[str(p) for p in suspect.index[:3]],
        )
    )


def _check_values(values: pd.Series, issues: list[QualityIssue]) -> None:
    """Value column problems that break the arithmetic."""
    parsed = values.dropna()
    if parsed.empty:
        issues.append(
            QualityIssue(
                "no_values",
                Severity.BLOCK,
                "No usable values",
                "Nothing in the value column could be read as a number.",
            )
        )
        return

    negative = parsed[parsed < 0]
    share = len(negative) / len(parsed)
    if share > 0.25:
        issues.append(
            QualityIssue(
                "mostly_negative",
                Severity.BLOCK,
                f"{share:.0%} of values are negative",
                (
                    "This column may be refunds or costs rather than revenue. "
                    "Totals built on it would be wrong."
                ),
                count=int(len(negative)),
            )
        )
    elif len(negative):
        issues.append(
            QualityIssue(
                "some_negative",
                Severity.INFO,
                f"{len(negative):,} rows have a negative value",
                "Usually refunds. They are included as they are.",
                count=int(len(negative)),
            )
        )

    if float((parsed == 0).mean()) > 0.5:
        issues.append(
            QualityIssue(
                "mostly_zero",
                Severity.WARN,
                "More than half the values are zero",
                "Most rows carry no value, so averages and totals will be thin.",
            )
        )


def _check_against_history(
    raw: pd.DataFrame,
    dates: pd.Series | None,
    values: pd.Series | None,
    previous: Snapshot,
    issues: list[QualityIssue],
) -> None:
    """Compare this refresh against the last one that passed."""
    if previous.rows > 0:
        ratio = len(raw) / previous.rows
        if ratio < 1 - ROW_DROP_BLOCK:
            issues.append(
                QualityIssue(
                    "row_count_collapse",
                    Severity.BLOCK,
                    f"This refresh has {1 - ratio:.0%} fewer rows than last time",
                    (
                        f"{len(raw):,} rows now versus {previous.rows:,} before. "
                        "A partial export looks exactly like a collapse in sales."
                    ),
                    count=len(raw),
                )
            )
        elif ratio < 1 - ROW_DROP_WARN:
            issues.append(
                QualityIssue(
                    "row_count_drop",
                    Severity.WARN,
                    f"This refresh has {1 - ratio:.0%} fewer rows than last time",
                    f"{len(raw):,} rows now versus {previous.rows:,} before.",
                    count=len(raw),
                )
            )

    if values is not None and previous.median_value > 0:
        parsed = values.dropna()
        parsed = parsed[parsed != 0]
        if len(parsed):
            median = float(parsed.median())
            ratio = median / previous.median_value
            if ratio > SHIFT_BLOCK or ratio < 1 / SHIFT_BLOCK:
                issues.append(
                    QualityIssue(
                        "distribution_shift",
                        Severity.BLOCK,
                        "The typical order value has changed dramatically",
                        (
                            f"Median is {median:,.0f} now versus "
                            f"{previous.median_value:,.0f} before. This is more "
                            "often a currency or units change than a real one."
                        ),
                    )
                )
            elif ratio > SHIFT_WARN or ratio < 1 / SHIFT_WARN:
                issues.append(
                    QualityIssue(
                        "value_shift",
                        Severity.WARN,
                        "The typical order value has moved a lot",
                        (
                            f"Median is {median:,.0f} now versus "
                            f"{previous.median_value:,.0f} before."
                        ),
                    )
                )

    if dates is not None and previous.date_max:
        parsed = dates.dropna()
        if len(parsed):
            try:
                before = pd.Timestamp(previous.date_max)
            except ValueError:
                return
            if parsed.max() < before:
                issues.append(
                    QualityIssue(
                        "history_went_backwards",
                        Severity.BLOCK,
                        "This refresh ends earlier than the last one",
                        (
                            f"Newest row is {parsed.max().date()} but the previous "
                            f"run reached {before.date()}. Data appears to have "
                            "been lost rather than added."
                        ),
                    )
                )


def _snapshot(
    raw: pd.DataFrame,
    dates: pd.Series | None,
    values: pd.Series | None,
    product_col: str | None,
) -> Snapshot:
    """Record what this run looked like, for the next one to compare against."""
    median = 0.0
    if values is not None:
        parsed = values.dropna()
        parsed = parsed[parsed != 0]
        if len(parsed):
            median = float(parsed.median())

    date_min = date_max = ""
    if dates is not None:
        parsed = dates.dropna()
        if len(parsed):
            date_min = str(parsed.min().date())
            date_max = str(parsed.max().date())

    products = 0
    if product_col and product_col in raw.columns:
        products = int(cleaning.to_text(raw[product_col]).nunique())

    return Snapshot(
        rows=int(len(raw)),
        median_value=median,
        products=products,
        date_min=date_min,
        date_max=date_max,
        taken_at=datetime.now(timezone.utc).isoformat(),
    )
