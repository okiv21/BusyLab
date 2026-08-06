"""Pillar 2: finding things and reaching out, instead of waiting to be opened.

This is the retention layer (spec Pillar 2), and its failure mode is not missing
an event. It is **alert fatigue**: a system that cries wolf gets muted, and a
muted alerting system protects nothing. Spec 11 lists sensitivity tuning as an
open question for exactly this reason. So the bias throughout is towards
silence, and four separate things hold it back:

**Robust statistics.** Anomalies are found with a median and a median absolute
deviation, not a mean and a standard deviation. A mean is dragged towards the
outlier being hunted and the standard deviation is inflated by it, so a single
genuine spike raises the bar and hides itself — and two spikes hide each other
completely.

**Seasonality first.** A December spike in a business that spikes every
December is not an anomaly. Where there is enough history to estimate a
seasonal shape, it is removed before anything is called unusual.

**Multiple comparisons.** Checking twenty products every week is twenty tests a
week, so roughly one false alarm a week by construction. Product-level
anomalies are FDR-corrected as a family, the same as segmentation and baskets.

**Materiality and a cap.** A statistically unusual move in a product that is
one percent of revenue is not worth an interruption, and no single run may emit
more than a handful of alerts however much it finds.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from .analysis import stats
from .analysis.dataset import PRODUCT, REVENUE, SalesFrame
from .findings import Finding, Severity as FindingSeverity
from .quality import QualityReport
from .quality import Severity as QualitySeverity

#: Iglewicz and Hoaglin's threshold for a robust z-score. Deliberately higher
#: than the familiar 3 sigma, because a weekly alert wants to be sure.
ROBUST_Z_THRESHOLD = 3.5
#: A move smaller than this is not worth an interruption, however clean.
MIN_RELATIVE_MOVE = 0.20
#: A product below this share of revenue does not get its own alert.
MIN_PRODUCT_SHARE = 0.05
#: Periods of history needed before "unusual" means anything.
MIN_HISTORY = 8
#: How many recent periods to examine. Older anomalies are not news.
RECENT_PERIODS = 2
#: Hard ceiling per run. Past this, the digest is the right channel.
MAX_ALERTS = 6


class AlertKind(str, Enum):
    ANOMALY_DROP = "anomaly_drop"
    ANOMALY_SPIKE = "anomaly_spike"
    FINDING = "finding"
    DATA_QUALITY = "data_quality"
    GOAL_OFF_TRACK = "goal_off_track"


class AlertLevel(str, Enum):
    """How loudly this asks for attention. Not an instruction to act."""

    HIGH = "high"
    MEDIUM = "medium"
    GOOD = "good"
    INFO = "info"


@dataclass
class Alert:
    """One thing worth telling the business about, unprompted."""

    kind: AlertKind
    level: AlertLevel
    title: str
    detail: str
    #: What the alert is about: a product, a channel, the business as a whole.
    subject: str = "the business"
    #: The period it concerns, so the same event is not re-sent next week.
    period: str = ""
    #: The finding a reader should be sent to, where one exists.
    finding_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    detected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def key(self) -> str:
        """Stable identity, so an alert is never sent twice.

        Deliberately excludes the detection time and the wording: the same
        event re-examined next week must produce the same key.
        """
        blob = f"{self.kind.value}|{self.subject}|{self.period}"
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind.value,
            "level": self.level.value,
            "title": self.title,
            "detail": self.detail,
            "subject": self.subject,
            "period": self.period,
            "finding_id": self.finding_id,
            "evidence": self.evidence,
            "detected_at": self.detected_at,
        }


# --------------------------------------------------------------------------
# Robust anomaly detection
# --------------------------------------------------------------------------


def robust_scores(values: np.ndarray) -> np.ndarray:
    """Modified z-scores using the median and the median absolute deviation.

    The textbook z-score is the wrong tool for finding outliers, because the
    outlier is inside both the mean and the standard deviation it is being
    measured against. One large spike inflates the deviation enough to hide
    itself; two spikes hide each other. The median and MAD are barely moved by
    a handful of extreme points, which is the whole reason to use them here.
    """
    if len(values) == 0:
        return np.array([])
    median = float(np.median(values))
    deviations = np.abs(values - median)
    mad = float(np.median(deviations))
    if mad == 0:
        # A perfectly flat history: fall back to the mean deviation so a
        # genuine break is still visible, rather than dividing by zero.
        mean_deviation = float(np.mean(deviations))
        if mean_deviation == 0:
            return np.zeros_like(values, dtype=float)
        return 0.7979 * (values - median) / mean_deviation
    return 0.6745 * (values - median) / mad


def _z_to_p(z: float) -> float:
    """Two-sided p-value for a robust z, so a family can be FDR-corrected."""
    from scipy import stats as scipy_stats

    return float(2.0 * scipy_stats.norm.sf(abs(z)))


def _deseasonalised(series: pd.Series) -> tuple[pd.Series, bool]:
    """Strip a repeating annual shape where there is enough history for one."""
    adjusted = stats.deseasonalize(series)
    if adjusted is None or len(adjusted) != len(series):
        return series, False
    return adjusted, True


@dataclass
class _Candidate:
    subject: str
    period: str
    value: float
    baseline: float
    z: float
    p_value: float
    share: float
    seasonally_adjusted: bool
    #: How many consecutive recent periods tripped the check.
    run_length: int = 1


def _scan(
    series: pd.Series, subject: str, share: float
) -> list[_Candidate]:
    """Look at the last few periods of one series for something unusual."""
    clean = series.dropna()
    if len(clean) < MIN_HISTORY:
        return []

    adjusted, was_adjusted = _deseasonalised(clean)
    values = adjusted.to_numpy(dtype=float)
    scores = robust_scores(values)
    if len(scores) == 0:
        return []

    baseline = float(np.median(values))
    out: list[_Candidate] = []
    for offset in range(1, min(RECENT_PERIODS, len(values)) + 1):
        index = len(values) - offset
        z = float(scores[index])
        if not np.isfinite(z) or abs(z) < ROBUST_Z_THRESHOLD:
            continue

        actual = float(clean.iloc[index])
        # Materiality against the untouched series, since that is what the
        # business actually experienced.
        reference = float(np.median(clean.to_numpy(dtype=float)))
        if reference <= 0:
            continue
        if abs(actual - reference) / reference < MIN_RELATIVE_MOVE:
            continue

        label = clean.index[index]
        out.append(
            _Candidate(
                subject=subject,
                period=str(pd.Timestamp(label).date())
                if isinstance(label, (pd.Timestamp, np.datetime64))
                else str(label),
                value=actual,
                baseline=reference,
                z=z,
                p_value=_z_to_p(z),
                share=share,
                seasonally_adjusted=was_adjusted,
            )
        )
    return out


def _collapse(candidates: list[_Candidate]) -> list[_Candidate]:
    """One alert per subject, not one per affected period.

    A sustained drop trips the check in every recent month it covers. Sending
    "revenue was low" twice in a row for the same slide is two notifications
    saying one thing, and the run of periods is more informative carried on a
    single alert than split across several.
    """
    if len(candidates) <= 1:
        return candidates

    by_subject: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        by_subject.setdefault(candidate.subject, []).append(candidate)

    out: list[_Candidate] = []
    for group in by_subject.values():
        strongest = max(group, key=lambda c: abs(c.z))
        strongest.run_length = len(group)
        out.append(strongest)
    return out


def detect_anomalies(frame: SalesFrame) -> list[Alert]:
    """Unusual spikes and drops in the most recent periods.

    The whole business is checked, then each material product. Product checks
    form one family and are FDR-corrected together, because twenty products
    examined weekly is twenty tests a week and about one false alarm a week if
    left uncorrected.
    """
    freq = frame.natural_frequency()
    alerts: list[Alert] = []

    # The business as a whole. One test, so no correction is needed.
    total = frame.by_period(freq=freq, value=REVENUE, drop_partial=True)
    for candidate in _collapse(_scan(total, "the business", 1.0)):
        alerts.append(_to_alert(candidate, freq, corrected=False))

    # Per product, as one family.
    by_product = frame.by_product(REVENUE)
    grand = float(by_product.sum())
    if grand <= 0:
        return alerts[:MAX_ALERTS]

    matrix = frame.product_period(freq=freq, value=REVENUE, drop_partial=True)
    candidates: list[_Candidate] = []
    for product, revenue in by_product.items():
        share = float(revenue / grand)
        if share < MIN_PRODUCT_SHARE or str(product) not in matrix.columns:
            continue
        candidates.extend(_collapse(_scan(matrix[str(product)], str(product), share)))

    if candidates:
        _, adjusted = stats.benjamini_hochberg([c.p_value for c in candidates])
        for candidate, adjusted_p in zip(candidates, adjusted):
            if adjusted_p < stats.ALPHA:
                alerts.append(
                    _to_alert(candidate, freq, corrected=True, adjusted_p=adjusted_p)
                )

    # Biggest moves first, then trim. A digest carries the rest.
    alerts.sort(key=lambda a: -abs(a.evidence.get("robust_z", 0.0)))
    return alerts[:MAX_ALERTS]


def _to_alert(
    candidate: _Candidate,
    freq: str,
    *,
    corrected: bool,
    adjusted_p: float | None = None,
) -> Alert:
    unit = {"MS": "month", "W": "week", "D": "day"}.get(freq, "period")
    dropped = candidate.value < candidate.baseline
    change = (candidate.value - candidate.baseline) / candidate.baseline

    subject_words = (
        "Revenue" if candidate.subject == "the business" else candidate.subject
    )
    run = (
        f" for {candidate.run_length} {unit}s running"
        if candidate.run_length > 1
        else ""
    )
    title = (
        f"{subject_words} came in {abs(change) * 100:.0f}% "
        f"{'below' if dropped else 'above'} its usual {unit}{run}"
    )

    detail_parts = [
        f"{candidate.value:,.0f} against a typical {unit} of "
        f"{candidate.baseline:,.0f}.",
        "This is outside the range this business normally moves in.",
    ]
    if candidate.seasonally_adjusted:
        detail_parts.append("Seasonal pattern was removed before checking.")
    if corrected:
        detail_parts.append(
            "Checked alongside every other product and still stands out."
        )

    return Alert(
        kind=AlertKind.ANOMALY_DROP if dropped else AlertKind.ANOMALY_SPIKE,
        level=AlertLevel.HIGH if dropped else AlertLevel.GOOD,
        title=title,
        detail=" ".join(detail_parts),
        subject=candidate.subject,
        period=candidate.period,
        evidence={
            "value": candidate.value,
            "baseline": candidate.baseline,
            "change_pct": change,
            "robust_z": candidate.z,
            "p_value": candidate.p_value,
            "adjusted_p": adjusted_p,
            "method": "modified z-score on median and MAD",
            "correction": "Benjamini-Hochberg FDR" if corrected else None,
            "seasonally_adjusted": candidate.seasonally_adjusted,
            "revenue_share": candidate.share,
        },
    )


# --------------------------------------------------------------------------
# Alerts from work already done
# --------------------------------------------------------------------------


def _first_sentence(text: str) -> str:
    """The opening sentence, without breaking on a decimal point.

    Splitting on "." alone turns "online fell by 553.5k per period" into
    "online fell by 553", so the boundary has to be a full stop followed by
    whitespace.
    """
    import re

    match = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    first = match[0].strip() if match else text.strip()
    return first if first.endswith((".", "!", "?")) else first + "."


def alerts_from_findings(findings: list[Finding]) -> list[Alert]:
    """Promote the findings that genuinely warrant an interruption.

    Only urgent ones, and only those the engine could actually confirm. An
    unconfirmed finding belongs in the story where it can be read in context,
    not in an alert that arrives out of the blue.
    """
    out: list[Alert] = []
    for finding in findings:
        if finding.severity is not FindingSeverity.URGENT:
            continue
        if finding.evidence.p_value is not None and not finding.is_significant:
            continue
        out.append(
            Alert(
                kind=AlertKind.FINDING,
                level=AlertLevel.HIGH,
                title=_first_sentence(finding.summary),
                detail=finding.summary,
                subject=str(
                    finding.facts.get("product")
                    or finding.facts.get("driver")
                    or "the business"
                ),
                period=str(finding.facts.get("frequency", "")) or "current",
                finding_id=finding.id,
                evidence={
                    "method": finding.evidence.method,
                    "p_value": finding.evidence.p_value,
                    "strength": finding.evidence.strength,
                },
            )
        )
    return out


def alerts_from_quality(report: QualityReport | None) -> list[Alert]:
    """Data quality alerts from the ingest gate (spec 4.3, Pillar 2).

    Once ingestion is unattended nobody is watching the gate, so a refresh that
    fails has to reach out rather than sit in a screen nobody opened.
    """
    if report is None:
        return []
    out: list[Alert] = []
    for issue in report.issues:
        if issue.severity is QualitySeverity.INFO:
            continue
        out.append(
            Alert(
                kind=AlertKind.DATA_QUALITY,
                level=(
                    AlertLevel.HIGH
                    if issue.severity is QualitySeverity.BLOCK
                    else AlertLevel.MEDIUM
                ),
                title=issue.title,
                detail=issue.detail,
                subject=issue.code,
                period="ingest",
                evidence={"code": issue.code, "count": issue.count},
            )
        )
    return out


def alerts_from_goals(findings: list[Finding]) -> list[Alert]:
    """A target that has slipped out of reach is worth saying unprompted."""
    out: list[Alert] = []
    for finding in findings:
        if not finding.id.startswith("goal_"):
            continue
        facts = finding.facts
        if facts.get("on_track") or facts.get("could_still_hit"):
            continue
        goal = facts.get("goal") or {}
        out.append(
            Alert(
                kind=AlertKind.GOAL_OFF_TRACK,
                level=AlertLevel.HIGH,
                title=(
                    f"{goal.get('label') or 'Your target'} is now out of reach "
                    f"at current pace"
                ),
                detail=finding.summary,
                subject=str(goal.get("id", finding.id)),
                period=str(goal.get("end", "")),
                finding_id=finding.id,
                evidence={
                    "share_of_target": facts.get("share_of_target"),
                    "gap": facts.get("gap"),
                },
            )
        )
    return out


def build_alerts(
    frame: SalesFrame | None,
    findings: list[Finding],
    quality: QualityReport | None = None,
    *,
    already_sent: set[str] | None = None,
) -> list[Alert]:
    """Everything worth reaching out about, deduplicated and capped.

    ``already_sent`` holds the keys of alerts this account has seen. Re-sending
    an alert every time the scheduler runs is the fastest possible route to
    being muted, so an alert fires once.
    """
    already_sent = already_sent or set()

    collected: list[Alert] = []
    collected.extend(alerts_from_quality(quality))
    # A failed gate means the findings are not trustworthy, so nothing else is
    # promoted from an analysis that was held.
    if quality is None or quality.passed:
        if frame is not None:
            collected.extend(detect_anomalies(frame))
        collected.extend(alerts_from_findings(findings))
        collected.extend(alerts_from_goals(findings))

    seen: set[str] = set()
    fresh: list[Alert] = []
    for alert in collected:
        key = alert.key
        if key in already_sent or key in seen:
            continue
        seen.add(key)
        fresh.append(alert)

    order = {
        AlertLevel.HIGH: 0,
        AlertLevel.MEDIUM: 1,
        AlertLevel.GOOD: 2,
        AlertLevel.INFO: 3,
    }
    fresh.sort(key=lambda a: order.get(a.level, 9))
    return fresh[:MAX_ALERTS]
