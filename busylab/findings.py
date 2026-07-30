"""What the engine emits.

A Finding is a structured, numeric, evidenced fact. It is *not* a sentence and
it is *not* advice.

Two rules from the spec shape this module:

**Insights, not directives (spec 2).** A finding states what is true and stops.
It never says "remove this product" or "raise the price". The decision stays
with the business owner, which is both respectful and the thing that makes
BusyLab safe for a platform to embed. :func:`check_non_directive` enforces this
mechanically, because it is the kind of rule that erodes one helpful sentence
at a time.

**The LLM only narrates (spec 2, 8).** Every number a finding carries is
computed here, deterministically. The narration layer may rewrite ``summary``
into better English, but it may never invent, adjust or recompute a value in
``facts``. Anything the story needs to say must therefore already be in
``facts``.

Chart choice is a deterministic mapping from the finding's type (spec 7), so
the shape of the insight picks the visual and nobody downstream has to guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .roles import Tier


class FindingType(str, Enum):
    """The shape of an insight. Drives the chart, per spec 7."""

    TREND = "trend"  # a real rise or fall over time
    NOISE = "noise"  # a move that is not distinguishable from variance
    RANKING = "ranking"  # who is biggest, in order
    CONCENTRATION = "concentration"  # how much sits in how few
    TENSION = "tension"  # two metrics disagreeing, revenue vs margin
    DECOMPOSITION = "decomposition"  # why a number moved
    SEASONALITY = "seasonality"  # a repeating annual shape
    SEGMENTATION = "segmentation"  # a difference across groups
    REPEAT_VS_NEW = "repeat_vs_new"
    FORECAST = "forecast"
    CUSTOMER_SEGMENTS = "customer_segments"
    COHORT_RETENTION = "cohort_retention"
    RELATIONSHIP = "relationship"  # products moving together
    GOAL_PACE = "goal_pace"
    DATA_QUALITY = "data_quality"


class ChartType(str, Enum):
    """The visual vocabulary. No 3D, no radar, no gauges (spec 7)."""

    LINE_WITH_BAND = "line_with_band"
    BAR_HORIZONTAL = "bar_horizontal"
    DONUT = "donut"
    TREEMAP = "treemap"
    SCATTER = "scatter"
    DIVERGING_BARS = "diverging_bars"
    WATERFALL = "waterfall"
    GROUPED_BARS = "grouped_bars"
    SMALL_MULTIPLES = "small_multiples"
    STACKED_AREA = "stacked_area"
    FORECAST_FAN = "forecast_fan"
    QUADRANT = "quadrant"
    COHORT_HEATMAP = "cohort_heatmap"
    CORRELATION_HEATMAP = "correlation_heatmap"
    PROGRESS_ARC = "progress_arc"
    CALLOUT = "callout"  # a number that needs no chart


#: Spec 7's table, as code. Composition picks donut or treemap by item count,
#: which is the one branch the table specifies, handled in :func:`chart_for`.
_CHART_BY_TYPE: dict[FindingType, ChartType] = {
    FindingType.TREND: ChartType.LINE_WITH_BAND,
    FindingType.NOISE: ChartType.LINE_WITH_BAND,
    FindingType.RANKING: ChartType.BAR_HORIZONTAL,
    FindingType.CONCENTRATION: ChartType.DONUT,
    FindingType.TENSION: ChartType.SCATTER,
    FindingType.DECOMPOSITION: ChartType.WATERFALL,
    FindingType.SEASONALITY: ChartType.LINE_WITH_BAND,
    FindingType.SEGMENTATION: ChartType.GROUPED_BARS,
    FindingType.REPEAT_VS_NEW: ChartType.STACKED_AREA,
    FindingType.FORECAST: ChartType.FORECAST_FAN,
    FindingType.CUSTOMER_SEGMENTS: ChartType.QUADRANT,
    FindingType.COHORT_RETENTION: ChartType.COHORT_HEATMAP,
    FindingType.RELATIONSHIP: ChartType.CORRELATION_HEATMAP,
    FindingType.GOAL_PACE: ChartType.PROGRESS_ARC,
    FindingType.DATA_QUALITY: ChartType.CALLOUT,
}

#: Above this many slices a donut becomes unreadable and a treemap wins.
TREEMAP_THRESHOLD = 6


def chart_for(finding_type: FindingType, item_count: int | None = None) -> ChartType:
    """Pick the chart from the shape of the finding, never from preference."""
    chart = _CHART_BY_TYPE[finding_type]
    if (
        finding_type is FindingType.CONCENTRATION
        and item_count is not None
        and item_count >= TREEMAP_THRESHOLD
    ):
        return ChartType.TREEMAP
    return chart


class Severity(str, Enum):
    """How much attention a finding is asking for.

    This is about salience, not instruction. ``WATCH`` means "this is moving
    against you", never "do something about it".
    """

    GOOD = "good"
    NEUTRAL = "neutral"
    WATCH = "watch"
    URGENT = "urgent"


@dataclass
class Evidence:
    """The statistical receipts behind a finding.

    Carried on every finding so that "this is real" is auditable rather than
    asserted, and so the narration layer can hedge honestly when it should.
    """

    method: str = ""
    p_value: float | None = None
    #: p-value after multiple-comparison correction, where one was applied.
    adjusted_p: float | None = None
    sample_size: int | None = None
    #: Set when the finding survived a family-wise correction (spec 3.3).
    correction: str | None = None
    confidence_low: float | None = None
    confidence_high: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_significant(self) -> bool:
        p = self.adjusted_p if self.adjusted_p is not None else self.p_value
        return p is not None and p < 0.05

    @property
    def strength(self) -> str:
        """Plain-language certainty, for the narration layer to lean on."""
        p = self.adjusted_p if self.adjusted_p is not None else self.p_value
        if p is None:
            return "descriptive"
        if p < 0.01:
            return "strong"
        if p < 0.05:
            return "clear"
        if p < 0.15:
            return "worth a look"
        return "not distinguishable from normal variation"


@dataclass
class Finding:
    """One evidenced, non-obvious fact about the business."""

    id: str
    type: FindingType
    #: A short, factual, non-directive sentence. The narration layer may
    #: rewrite this into better English but may not change what it claims.
    summary: str
    #: Every number the story needs. The LLM reads from here and never
    #: computes; anything absent here cannot be said downstream.
    facts: dict[str, Any] = field(default_factory=dict)
    evidence: Evidence = field(default_factory=Evidence)
    severity: Severity = Severity.NEUTRAL
    #: 0..1, used to rank the story. Set by the analysis that produced it.
    importance: float = 0.5
    tier: Tier = Tier.CORE
    #: Chart payload: whatever the mapped chart type needs to draw itself.
    chart_data: dict[str, Any] = field(default_factory=dict)
    chart: ChartType | None = None
    #: Ids of findings that are part of the same story, so the UI can link
    #: "Finding 1 and this are the same story".
    related: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.chart is None:
            items = self.facts.get("item_count")
            self.chart = chart_for(self.type, items if isinstance(items, int) else None)

    @property
    def is_significant(self) -> bool:
        return self.evidence.is_significant

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-able form, for the API and for cached narration."""
        return {
            "id": self.id,
            "type": self.type.value,
            "chart": self.chart.value if self.chart else None,
            "summary": self.summary,
            "facts": self.facts,
            "severity": self.severity.value,
            "importance": round(self.importance, 4),
            "tier": self.tier.value,
            "evidence": {
                "method": self.evidence.method,
                "p_value": self.evidence.p_value,
                "adjusted_p": self.evidence.adjusted_p,
                "sample_size": self.evidence.sample_size,
                "correction": self.evidence.correction,
                "strength": self.evidence.strength,
                "notes": self.evidence.notes,
            },
            "chart_data": self.chart_data,
            "related": self.related,
        }


# --------------------------------------------------------------------------
# The non-directive guard
# --------------------------------------------------------------------------

#: Phrasings that turn an observation into an instruction. Kept explicit so
#: the rule is reviewable rather than a matter of taste.
_DIRECTIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\byou should\b", "tells the owner what to do"),
    (r"\byou must\b", "tells the owner what to do"),
    (r"\byou need to\b", "tells the owner what to do"),
    (r"\bwe recommend\b", "gives advice"),
    (r"\bwe suggest\b", "gives advice"),
    (r"\bour advice\b", "gives advice"),
    (r"\bconsider (?:raising|lowering|dropping|removing|cutting)\b", "gives advice"),
    (r"\b(?:try|start|stop) (?:raising|lowering|selling|discounting)\b", "gives advice"),
    (r"\bworth (?:raising|cutting|dropping|removing)\b", "gives advice"),
    (r"\bshould be (?:raised|lowered|removed|dropped|cut)\b", "gives advice"),
    (r"\bneeds? a (?:backup|price|discount) \w+\b", "gives advice"),
    (r"\bbundle idea\b", "gives advice"),
    (r"\b(?:raise|lower|cut|drop|remove|discontinue) (?:the |your )?price\b", "gives advice"),
)


class DirectiveLanguageError(AssertionError):
    """Raised when a finding crosses from illuminating into instructing."""


def check_non_directive(text: str) -> list[str]:
    """Return the reasons ``text`` reads as advice. Empty means it is clean.

    Spec 2 is unambiguous that BusyLab surfaces findings and leaves the
    decision entirely with the owner: directive AI carries liability, whereas
    illuminating AI is trusted. This is the check that keeps that true as the
    copy grows.
    """
    lowered = text.lower()
    return [why for pattern, why in _DIRECTIVE_PATTERNS if re.search(pattern, lowered)]


def assert_non_directive(text: str) -> None:
    """Raise if ``text`` instructs rather than informs."""
    problems = check_non_directive(text)
    if problems:
        raise DirectiveLanguageError(f"{text!r} {problems[0]}")
