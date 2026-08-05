"""Run every available analysis and rank the results into a story.

The output is ordered, because the interface is a narrative rather than a grid
(spec 6): most important first, each finding a visual plus one plain sentence.
Ranking is therefore part of the engine, not a presentation detail.

An analysis that cannot run on the available columns is skipped silently and
reported as a locked tier instead, which is what drives the greyed-out "add a
cost column to unlock profit insights" prompts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from ..detection.engine import DetectionResult, detect
from ..findings import Finding, Severity, check_non_directive
from ..quality import QualityReport
from ..quality import check as check_quality
from ..roles import TIER_SPECS, Tier
from . import core, forecast, segments
from .dataset import SalesFrame, build

log = logging.getLogger(__name__)

#: Every analysis, in no particular order. Ranking happens afterwards.
ANALYSES: tuple[Callable[[SalesFrame], list[Finding]], ...] = (
    core.revenue_trend,
    core.seasonality,
    core.margin_reality,
    core.concentration_risk,
    core.revenue_decomposition,
    core.dimension_decomposition,
    core.price_volume_split,
    core.product_ranking,
    segments.segmentation,
    segments.product_relationships,
    forecast.revenue_forecast,
    forecast.product_forecasts,
)

#: Severity nudges the order: a real decline outranks a pleasant fact of equal
#: statistical weight, because the story should lead with what needs attention.
_SEVERITY_WEIGHT: dict[Severity, float] = {
    Severity.URGENT: 0.15,
    Severity.WATCH: 0.08,
    Severity.NEUTRAL: 0.0,
    Severity.GOOD: 0.02,
}


@dataclass
class AnalysisResult:
    """The story, plus everything needed to explain what was and was not run."""

    findings: list[Finding] = field(default_factory=list)
    frame: SalesFrame | None = None
    detection: DetectionResult | None = None
    tiers: dict[Tier, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    quality: QualityReport | None = None

    @property
    def held(self) -> bool:
        """True when the quality gate stopped this analysis being published."""
        return self.quality is not None and not self.quality.passed

    @property
    def headline(self) -> Finding | None:
        """The one finding that earns the hero treatment (spec 7)."""
        return self.findings[0] if self.findings else None

    @property
    def significant(self) -> list[Finding]:
        return [f for f in self.findings if f.is_significant]

    def locked(self) -> list[tuple[Tier, str]]:
        """Tiers not unlocked, with the prompt that would unlock each."""
        return [
            (tier, TIER_SPECS[tier].locked_prompt)
            for tier, ok in self.tiers.items()
            if not ok and TIER_SPECS[tier].locked_prompt
        ]

    def to_dict(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "tiers": {t.value: ok for t, ok in self.tiers.items()},
            "locked": [
                {"tier": t.value, "prompt": prompt} for t, prompt in self.locked()
            ],
            "notes": self.notes,
            "errors": self.errors,
            "held": self.held,
            "quality": self.quality.to_dict() if self.quality else None,
        }


def rank(findings: Iterable[Finding]) -> list[Finding]:
    """Order findings for the story: most important first.

    Importance comes from the analysis that produced the finding, then gets a
    nudge for severity and a penalty for anything that failed its own
    significance test. A finding the engine could not confirm should never sit
    above one it could.
    """

    def score(finding: Finding) -> float:
        value = finding.importance + _SEVERITY_WEIGHT.get(finding.severity, 0.0)
        if finding.evidence.p_value is not None and not finding.is_significant:
            value -= 0.2
        return value

    return sorted(findings, key=lambda f: (-score(f), f.id))


def analyse(
    raw: pd.DataFrame,
    detection: DetectionResult | None = None,
    *,
    strict: bool = False,
    previous_snapshot: dict | None = None,
    skip_quality_gate: bool = False,
) -> AnalysisResult:
    """Run the engine over a raw frame.

    ``strict`` re-raises analysis errors instead of collecting them, which is
    what the test suite wants and what production does not: one failing
    analysis should never take the whole story down.

    ``previous_snapshot`` is the quality snapshot from the last run that
    passed, which is what makes "the row count halved" answerable.
    """
    detection = detection if detection is not None else detect(raw)
    result = AnalysisResult(detection=detection, tiers=dict(detection.tiers))

    if detection.missing:
        missing = ", ".join(sorted(r.value for r in detection.missing))
        result.errors.append(f"Cannot analyse without: {missing}")
        return result

    # The gate runs before anything is computed, let alone published. A
    # refresh that fails holds the analysis rather than publishing a
    # confidently wrong insight (spec 4.3).
    if not skip_quality_gate:
        result.quality = check_quality(
            raw, detection.assignments, previous=previous_snapshot
        )
        if not result.quality.passed:
            result.notes.append(result.quality.headline)
            return result

    try:
        frame = build(raw, detection)
    except ValueError as exc:
        result.errors.append(str(exc))
        return result

    result.frame = frame
    result.notes.extend(frame.notes)

    collected: list[Finding] = []
    for analysis in ANALYSES:
        try:
            collected.extend(analysis(frame) or [])
        except Exception as exc:  # one bad analysis must not sink the story
            if strict:
                raise
            log.warning("analysis %s failed: %s", analysis.__name__, exc)
            result.errors.append(f"{analysis.__name__} could not run: {exc}")

    for finding in collected:
        problems = check_non_directive(finding.summary)
        if problems:
            # Spec 2 is a hard rule, so a directive sentence is a bug, not a
            # style issue. Loud in tests, contained in production.
            message = f"finding {finding.id!r} reads as advice: {problems[0]}"
            if strict:
                raise AssertionError(message)
            log.error(message)
            result.errors.append(message)

    result.findings = rank(collected)
    return result


def analyse_file(path: str | Path, *, strict: bool = False) -> AnalysisResult:
    """Load a spreadsheet and analyse it. The whole product in one call."""
    from .. import loading

    frame, report = loading.load(Path(path))
    result = analyse(frame, strict=strict)
    result.notes.insert(0, report.summary())
    result.notes.extend(report.notes)
    return result
