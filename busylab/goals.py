"""Pillar 4: goal and target tracking.

The business sets a revenue or profit target for a window, and BusyLab tracks
pace and projects the outcome (spec Pillar 4). This ties a prediction directly
to a decision the owner already cares about, which is why it earns its place
over a more sophisticated analysis nobody asked for.

The example output in the spec sets the bar: *"At current pace you will reach
87 percent of your Q1 target, and the gap is entirely Product 4's decline."*
Two halves — the projection, and the attribution. A number on its own tells the
owner they have a problem; the second half tells them where it lives.

Three honesty rules carry over from the rest of the engine:

* **The projection carries a band.** Reusing the ARIMA machinery means a goal
  outcome is a range, not a single confident figure.
* **"Now" is the last sale in the file**, never today's date. A file uploaded
  three weeks late must not report three weeks of zero sales as a collapse in
  pace.
* **The attribution has to add up.** The gap is decomposed into per-product
  changes that sum to it, rather than naming a plausible-sounding culprit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

#: Metrics a target can be set against. Both are already computed per row.
GOAL_METRICS = ("revenue", "profit")


@dataclass
class Goal:
    """A target the business has set for itself."""

    id: str
    metric: str  # "revenue" or "profit"
    target: float
    start: date
    end: date
    label: str = ""

    def __post_init__(self) -> None:
        if self.metric not in GOAL_METRICS:
            raise ValueError(
                f"metric must be one of {GOAL_METRICS}, got {self.metric!r}"
            )
        if self.target <= 0:
            raise ValueError("A target must be a positive amount.")
        if self.end < self.start:
            raise ValueError("A goal cannot end before it starts.")

    @property
    def name(self) -> str:
        return self.label or f"{self.metric.title()} target"

    @property
    def total_days(self) -> int:
        return (self.end - self.start).days + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "metric": self.metric,
            "target": self.target,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Goal":
        return cls(
            id=str(raw["id"]),
            metric=str(raw["metric"]),
            target=float(raw["target"]),
            start=pd.Timestamp(raw["start"]).date(),
            end=pd.Timestamp(raw["end"]).date(),
            label=str(raw.get("label", "")),
        )


@dataclass
class Contribution:
    """One product's share of why a goal is off pace."""

    product: str
    change: float

    def to_dict(self) -> dict[str, Any]:
        return {"product": self.product, "change": self.change}


@dataclass
class GoalProgress:
    """Where a goal stands, and where it is heading."""

    goal: Goal
    #: The metric total inside the window so far.
    actual: float
    #: Fraction of the window that has elapsed, 0 to 1.
    elapsed: float
    #: Projected total by the end of the window, and its 80% range.
    projected: float
    projected_low: float
    projected_high: float
    #: "not_started", "in_progress" or "finished".
    state: str
    #: How the projection was made, for the evidence line.
    method: str
    #: Periods inside the window, for the pace chart.
    pace: list[dict[str, Any]] = field(default_factory=list)
    #: Products whose movement explains a shortfall, largest first.
    gap_drivers: list[Contribution] = field(default_factory=list)
    as_of: str = ""

    @property
    def share_of_target(self) -> float:
        return self.projected / self.goal.target if self.goal.target else 0.0

    @property
    def actual_share(self) -> float:
        return self.actual / self.goal.target if self.goal.target else 0.0

    @property
    def gap(self) -> float:
        """Shortfall against the target. Negative means a surplus."""
        return self.goal.target - self.projected

    @property
    def on_track(self) -> bool:
        return self.projected >= self.goal.target

    @property
    def could_still_hit(self) -> bool:
        """True when the target sits inside the projected range.

        The difference between "you will miss this" and "you might miss this"
        matters, and a single projected number cannot express it.
        """
        return self.projected_high >= self.goal.target

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal.to_dict(),
            "actual": self.actual,
            "elapsed": self.elapsed,
            "projected": self.projected,
            "projected_low": self.projected_low,
            "projected_high": self.projected_high,
            "share_of_target": self.share_of_target,
            "actual_share": self.actual_share,
            "gap": self.gap,
            "on_track": self.on_track,
            "could_still_hit": self.could_still_hit,
            "state": self.state,
            "method": self.method,
            "pace": self.pace,
            "gap_drivers": [c.to_dict() for c in self.gap_drivers],
            "as_of": self.as_of,
        }
