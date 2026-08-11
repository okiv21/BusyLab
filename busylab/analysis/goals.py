"""Tracking pace against a target, and explaining the gap.

See :mod:`busylab.goals` for the model and the reasoning. This module does the
arithmetic against a :class:`~busylab.analysis.dataset.SalesFrame`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..findings import Evidence, Finding, FindingType, Severity
from ..goals import Contribution, Goal, GoalProgress
from ..roles import Tier
from . import forecast as forecast_module
from .dataset import DATE, PRODUCT, PROFIT, REVENUE, SalesFrame

#: Below this much of the window elapsed, a projection is mostly noise.
MIN_ELAPSED = 0.08


def _metric_column(frame: SalesFrame, metric: str) -> str | None:
    if metric == "profit":
        return PROFIT if frame.has_profit else None
    return REVENUE if frame.has(REVENUE) else None


def measure(frame: SalesFrame, goal: Goal) -> GoalProgress | None:
    """Work out where a goal stands and where it is heading."""
    column = _metric_column(frame, goal.metric)
    if column is None:
        return None

    data = frame.data[[DATE, PRODUCT, column]].dropna()
    if data.empty:
        return None

    # "Now" is the last sale in the file, not today. A file uploaded three
    # weeks late must not read as three weeks of zero sales.
    as_of = data[DATE].max()
    start = pd.Timestamp(goal.start)
    end = pd.Timestamp(goal.end)

    if as_of < start:
        return GoalProgress(
            goal=goal,
            actual=0.0,
            elapsed=0.0,
            projected=0.0,
            projected_low=0.0,
            projected_high=0.0,
            state="not_started",
            method="the window has not begun",
            as_of=str(as_of.date()),
        )

    window = data[(data[DATE] >= start) & (data[DATE] <= end)]
    actual = float(window[column].sum())

    reached = min(as_of, end)
    elapsed_days = (reached - start).days + 1
    elapsed = max(0.0, min(1.0, elapsed_days / goal.total_days))

    if as_of >= end:
        # Nothing left to project; the answer is known.
        return GoalProgress(
            goal=goal,
            actual=actual,
            elapsed=1.0,
            projected=actual,
            projected_low=actual,
            projected_high=actual,
            state="finished",
            method="the window has closed, so this is the actual total",
            pace=_pace_rows(window, column, goal),
            gap_drivers=_gap_drivers(window, column) if actual < goal.target else [],
            as_of=str(as_of.date()),
        )

    if elapsed < MIN_ELAPSED:
        return GoalProgress(
            goal=goal,
            actual=actual,
            elapsed=elapsed,
            projected=0.0,
            projected_low=0.0,
            projected_high=0.0,
            state="in_progress",
            method="too early in the window to project",
            pace=_pace_rows(window, column, goal),
            as_of=str(as_of.date()),
        )

    projected, low, high, method = _project_remainder(
        frame, column, goal, actual, reached, end, elapsed
    )

    return GoalProgress(
        goal=goal,
        actual=actual,
        elapsed=elapsed,
        projected=projected,
        projected_low=low,
        projected_high=high,
        state="in_progress",
        method=method,
        pace=_pace_rows(window, column, goal),
        gap_drivers=_gap_drivers(window, column) if projected < goal.target else [],
        as_of=str(as_of.date()),
    )


def _project_remainder(
    frame: SalesFrame,
    column: str,
    goal: Goal,
    actual: float,
    reached: pd.Timestamp,
    end: pd.Timestamp,
    elapsed: float,
) -> tuple[float, float, float, str]:
    """Project the rest of the window, preferring a model with a band.

    The ARIMA machinery already produces intervals, so a goal outcome comes out
    as a range. When there is not enough history for a model, this falls back to
    a plain run-rate extrapolation and says so, rather than dressing a straight
    line up as a forecast.
    """
    remaining_days = (end - reached).days
    if remaining_days <= 0:
        return actual, actual, actual, "the window has closed"

    freq = frame.natural_frequency()
    series = frame.by_period(freq=freq, value=column, drop_partial=True)
    projection = forecast_module.project(series, horizon=6, floor=None)

    if projection is not None and projection.trustworthy:
        # Convert projected period totals into a daily rate, then take only
        # the days that actually fall inside the goal window.
        days_per_period = {"MS": 30.44, "W": 7.0, "D": 1.0}.get(freq, 30.44)
        needed = remaining_days / days_per_period

        def accumulate(values: list[float]) -> float:
            whole = int(np.floor(needed))
            total = float(sum(values[: min(whole, len(values))]))
            fraction = needed - whole
            if fraction > 0 and whole < len(values):
                total += values[whole] * fraction
            elif whole >= len(values) and values:
                # Beyond the horizon, carry the final period forward.
                total += values[-1] * (needed - len(values))
            return total

        return (
            actual + accumulate(projection.mean),
            actual + accumulate(projection.lower80),
            actual + accumulate(projection.upper80),
            f"{projection.model_name} over the remaining period",
        )

    # Run-rate fallback. The band comes from how much the periods inside the
    # window have actually varied, so it is at least grounded in this business.
    rate = actual / max(elapsed, 1e-9)
    variability = 0.0
    if len(series) >= 3:
        mean = float(series.mean())
        if mean > 0:
            variability = float(series.std()) / mean
    spread = rate * min(variability, 0.6)
    return (
        rate,
        max(actual, rate - spread),
        rate + spread,
        "current run rate, with a range from how much periods have varied",
    )


def _pace_rows(
    window: pd.DataFrame, column: str, goal: Goal
) -> list[dict[str, object]]:
    """Per-month actual against the pace the target needs."""
    if window.empty:
        return []
    monthly = window.set_index(DATE)[column].resample("MS").sum()
    months = max(1, int(np.ceil(goal.total_days / 30.44)))
    needed = goal.target / months
    return [
        {
            "period": str(pd.Timestamp(index).date()),
            "actual": float(value),
            "needed": float(needed),
            "ahead": bool(value >= needed),
        }
        for index, value in monthly.items()
    ]


def _gap_drivers(window: pd.DataFrame, column: str) -> list[Contribution]:
    """Which products' movement explains a shortfall.

    Compares each product's run rate in the second half of the elapsed window
    against the first. The contributions sum to the total change, so the
    attribution adds up rather than naming a plausible culprit.
    """
    if window.empty:
        return []
    monthly = window.pivot_table(
        index=pd.Grouper(key=DATE, freq="MS"),
        columns=PRODUCT,
        values=column,
        aggfunc="sum",
    ).fillna(0.0)
    if len(monthly) < 4:
        return []

    half = len(monthly) // 2
    earlier = monthly.iloc[:half].mean()
    later = monthly.iloc[half:].mean()
    delta = (later - earlier).sort_values()

    drivers = [
        Contribution(product=str(product), change=float(change))
        for product, change in delta.items()
        if change < 0
    ]
    return drivers[:5]


def goal_pace(frame: SalesFrame, goals: list[Goal]) -> list[Finding]:
    """One finding per goal that has something to say."""
    findings: list[Finding] = []

    for goal in goals:
        progress = measure(frame, goal)
        if progress is None:
            continue
        # "Nothing to say yet" used to mean saying nothing at all, which from
        # the outside is indistinguishable from the target not being tracked.
        # Someone who sets a target and gets silence back has no way to tell
        # whether it is being measured, so these two states now report the
        # reason rather than being skipped.
        findings.append(_to_finding(progress))

    return findings


#: Past this multiple of the target, a percentage stops being readable.
_ABSURD_SHARE = 3.0


def _share_words(share: float) -> str:
    """How far along a target is, in words that survive a bad target.

    A target typed in as 70,000 for a business turning over millions produces
    "137413% of target", which is correct and tells the reader nothing except
    that something looks broken. Past three times the target the multiple is
    the honest summary, and the exact figure sits in the sentence anyway.

    Returns a fragment that expects the target to follow it, so callers read
    "... lands about 45% of the 40,000,000 target".
    """
    if share >= _ABSURD_SHARE:
        return f"about {share:.0f} times"
    return f"about {share:.0%} of"


def _to_finding(progress: GoalProgress) -> Finding:
    goal = progress.goal
    share = progress.share_of_target
    metric_word = goal.metric

    driver_clause = ""
    if progress.gap_drivers and progress.gap > 0:
        biggest = progress.gap_drivers[0]
        covered = abs(biggest.change) / abs(progress.gap) if progress.gap else 0
        if covered >= 0.6:
            driver_clause = (
                f" The gap is almost entirely {biggest.product}, which is down "
                f"{abs(biggest.change):,.0f} a month."
            )
        elif len(progress.gap_drivers) > 1:
            names = " and ".join(c.product for c in progress.gap_drivers[:2])
            driver_clause = f" The shortfall sits mostly in {names}."

    if progress.state == "not_started":
        # Almost always a window typed in for next quarter while the file stops
        # at last month. Saying so is the whole value: a reader looking at an
        # empty target panel cannot tell that apart from a broken feature.
        summary = (
            f"{goal.name} covers {goal.start} to {goal.end}, which starts after "
            f"the last sale in your file ({progress.as_of}). Nothing has been "
            f"measured against it yet - upload data from that window and it "
            f"will start reporting."
        )
        return Finding(
            id=f"goal_{goal.id}",
            type=FindingType.GOAL_PACE,
            summary=summary,
            facts=progress.to_dict(),
            evidence=Evidence(method="the window has not begun"),
            severity=Severity.NEUTRAL,
            importance=0.2,
            tier=Tier.CORE,
        )

    if progress.state == "in_progress" and progress.projected <= 0:
        # Genuinely too early to project, which is different from not tracking.
        summary = (
            f"{goal.name} has {progress.actual:,.0f} of its "
            f"{goal.target:,.0f} {metric_word} target so far, but only "
            f"{progress.elapsed:.0%} of the window has passed - too little to "
            f"say where it will land."
        )
        return Finding(
            id=f"goal_{goal.id}",
            type=FindingType.GOAL_PACE,
            summary=summary,
            facts=progress.to_dict(),
            evidence=Evidence(method="too early in the window to project"),
            severity=Severity.NEUTRAL,
            importance=0.3,
            tier=Tier.CORE,
        )

    if progress.state == "finished":
        if progress.actual >= goal.target:
            summary = (
                f"{goal.name} finished at {progress.actual:,.0f} against a "
                f"target of {goal.target:,.0f} - {_share_words(share)} it."
            )
            severity = Severity.GOOD
        else:
            summary = (
                f"{goal.name} finished at {progress.actual:,.0f} against a "
                f"target of {goal.target:,.0f} - {_share_words(share)} it."
                f"{driver_clause}"
            )
            severity = Severity.WATCH
        importance = 0.7
    elif progress.on_track:
        summary = (
            f"{goal.name} is on course: {progress.actual:,.0f} {metric_word} so "
            f"far against a target of {goal.target:,.0f}, which at this pace "
            f"lands at {_share_words(share)} it."
        )
        severity = Severity.GOOD
        importance = 0.72
    elif progress.could_still_hit:
        summary = (
            f"{goal.name} is behind: at this pace it lands "
            f"{_share_words(share)} the {goal.target:,.0f} target, though the "
            f"range still "
            f"reaches {progress.projected_high:,.0f} so it is not out of "
            f"reach.{driver_clause}"
        )
        severity = Severity.WATCH
        importance = 0.8
    else:
        summary = (
            f"{goal.name} will not be met at this pace: it lands "
            f"{_share_words(share)} the {goal.target:,.0f} {metric_word} "
            f"target, short by {progress.gap:,.0f} even at the most optimistic "
            f"end of the range.{driver_clause}"
        )
        severity = Severity.URGENT
        importance = 0.86

    return Finding(
        id=f"goal_{goal.id}",
        type=FindingType.GOAL_PACE,
        summary=summary,
        facts=progress.to_dict(),
        evidence=Evidence(
            method=f"pace against target, projected by {progress.method}",
            confidence_low=progress.projected_low,
            confidence_high=progress.projected_high,
            notes=[
                f"Measured against the last sale in the file ({progress.as_of}), "
                "not today's date.",
                f"{progress.elapsed:.0%} of the goal window has elapsed.",
            ]
            + (
                ["Gap attribution compares each product's recent run rate "
                 "against its earlier one; the parts sum to the total change."]
                if progress.gap_drivers
                else []
            ),
        ),
        severity=severity,
        importance=importance,
        tier=Tier.MARGIN if goal.metric == "profit" else Tier.CORE,
        chart_data={
            "actual": progress.actual,
            "target": goal.target,
            "projected": progress.projected,
            "projected_low": progress.projected_low,
            "projected_high": progress.projected_high,
            "elapsed": progress.elapsed,
            "pace": progress.pace,
            "state": progress.state,
        },
    )
