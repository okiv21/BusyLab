"""Data quality gate tests.

The gate exists because bad data arrives looking exactly as confident as good
data (spec 4.3). The single most important case here is the partial export: a
refresh carrying half the rows is indistinguishable from a business that lost
half its sales, and publishing the second interpretation of the first situation
is the worst thing this product could do.
"""

from __future__ import annotations

import pandas as pd
import pytest

from busylab.analysis import analyse
from busylab.detection import detect
from busylab.quality import Severity, Snapshot, check
from busylab.roles import Role

from . import fixtures


def _assignments(frame: pd.DataFrame) -> dict[Role, str]:
    return detect(frame).assignments


def _codes(report) -> set[str]:
    return {i.code for i in report.issues}


def _blocking_codes(report) -> set[str]:
    return {i.code for i in report.blocking}


# --------------------------------------------------------------------------
# Healthy data passes without noise
# --------------------------------------------------------------------------


def test_good_data_passes_cleanly() -> None:
    frame = fixtures.planted_business()
    report = check(frame, _assignments(frame))

    assert report.passed
    assert report.blocking == []
    assert report.headline == "This data looks healthy."


def test_a_passing_run_records_a_snapshot() -> None:
    frame = fixtures.planted_business()
    report = check(frame, _assignments(frame))

    assert report.snapshot is not None
    assert report.snapshot.rows == len(frame)
    assert report.snapshot.median_value > 0
    assert report.snapshot.date_min < report.snapshot.date_max


# --------------------------------------------------------------------------
# The failures that matter
# --------------------------------------------------------------------------


def test_a_partial_export_is_held_not_analysed() -> None:
    """The case the gate exists for.

    Half the rows arriving looks identical to half the sales disappearing.
    Analysing it would produce a confident, wrong, alarming story.
    """
    frame = fixtures.planted_business()
    good = check(frame, _assignments(frame))

    partial = frame.head(len(frame) // 3)
    report = check(partial, _assignments(frame), previous=good.snapshot)

    assert not report.passed
    assert "row_count_collapse" in _blocking_codes(report)


def test_a_duplicated_import_is_caught() -> None:
    frame = fixtures.planted_business()
    doubled = pd.concat([frame, frame], ignore_index=True)
    report = check(doubled, _assignments(frame))

    assert not report.passed
    assert "duplicate_rows" in _blocking_codes(report)


def test_a_null_spike_in_a_required_column_blocks() -> None:
    frame = fixtures.planted_business().copy()
    frame.loc[frame.index[: len(frame) // 2], "total_paid"] = None
    report = check(frame, _assignments(fixtures.planted_business()))

    assert not report.passed
    assert "null_spike" in _blocking_codes(report)


def test_a_missing_month_in_the_middle_blocks() -> None:
    """A trend drawn across a gap measures the gap."""
    frame = fixtures.planted_business()
    dates = pd.to_datetime(frame["order_date"])
    gapped = frame[~dates.dt.to_period("M").isin(
        [pd.Period("2024-06"), pd.Period("2024-07")]
    )]
    report = check(gapped, _assignments(frame))

    assert not report.passed
    assert "date_gaps" in _blocking_codes(report)


def test_a_gap_at_the_end_is_not_flagged() -> None:
    """The current month simply is not over yet."""
    frame = fixtures.planted_business()
    trimmed = frame[pd.to_datetime(frame["order_date"]) < "2025-06-01"]
    report = check(trimmed, _assignments(frame))

    assert "date_gaps" not in _codes(report)


def test_future_dates_are_caught() -> None:
    frame = fixtures.planted_business().copy()
    frame["order_date"] = pd.to_datetime(frame["order_date"])
    frame.loc[frame.index[:400], "order_date"] = pd.Timestamp.now() + pd.Timedelta(
        days=400
    )
    report = check(frame, _assignments(fixtures.planted_business()))

    assert "future_dates" in _blocking_codes(report)


def test_a_currency_change_reads_as_a_distribution_shift() -> None:
    """Values 100x larger is a units change far more often than real growth."""
    frame = fixtures.planted_business()
    good = check(frame, _assignments(frame))

    rescaled = frame.copy()
    rescaled["total_paid"] = rescaled["total_paid"] * 100
    report = check(rescaled, _assignments(frame), previous=good.snapshot)

    assert not report.passed
    assert "distribution_shift" in _blocking_codes(report)


def test_losing_history_blocks() -> None:
    frame = fixtures.planted_business()
    good = check(frame, _assignments(frame))

    older = frame[pd.to_datetime(frame["order_date"]) < "2024-06-01"]
    report = check(older, _assignments(frame), previous=good.snapshot)

    assert "history_went_backwards" in _blocking_codes(report)


def test_a_value_column_that_is_mostly_negative_blocks() -> None:
    frame = fixtures.planted_business().copy()
    frame["total_paid"] = -frame["total_paid"]
    report = check(frame, _assignments(fixtures.planted_business()))

    assert not report.passed
    assert "mostly_negative" in _blocking_codes(report)


def test_an_empty_file_blocks() -> None:
    frame = fixtures.planted_business()
    report = check(frame.head(0), _assignments(frame))

    assert not report.passed
    assert "empty_file" in _blocking_codes(report)


# --------------------------------------------------------------------------
# Proportionality: a gate nobody leaves on is worthless
# --------------------------------------------------------------------------


def test_a_handful_of_refunds_does_not_block() -> None:
    frame = fixtures.planted_business().copy()
    frame.loc[frame.index[:20], "total_paid"] *= -1
    report = check(frame, _assignments(fixtures.planted_business()))

    assert report.passed
    assert "some_negative" in _codes(report)


def test_a_small_row_count_drop_warns_rather_than_blocks() -> None:
    frame = fixtures.planted_business()
    good = check(frame, _assignments(frame))

    # Sampled rather than truncated: taking the head would also cut the newest
    # dates off, which is a different problem and blocks on its own.
    slightly_fewer = frame.sample(frac=0.7, random_state=3).sort_index()
    report = check(slightly_fewer, _assignments(frame), previous=good.snapshot)

    assert report.passed, "a 30% drop is worth saying, not worth stopping for"
    assert "row_count_drop" in _codes(report)


def test_history_checks_are_skipped_on_a_first_upload() -> None:
    """With no baseline, the gate does not guess at one."""
    frame = fixtures.planted_business()
    report = check(frame, _assignments(frame), previous=None)

    assert report.passed
    assert not any(
        c in _codes(report)
        for c in ("row_count_collapse", "distribution_shift", "history_went_backwards")
    )


# --------------------------------------------------------------------------
# The gate actually gates
# --------------------------------------------------------------------------


def test_a_held_analysis_publishes_no_findings() -> None:
    """Spec 4.3: hold the analysis and raise a flag, do not publish."""
    frame = fixtures.planted_business()
    good = check(frame, _assignments(frame))

    partial = frame.head(len(frame) // 3)
    result = analyse(partial, previous_snapshot=good.snapshot.to_dict())

    assert result.held
    assert result.findings == []
    assert result.quality is not None
    assert result.quality.blocking


def test_the_held_report_says_what_went_wrong() -> None:
    frame = fixtures.planted_business()
    good = check(frame, _assignments(frame))
    partial = frame.head(len(frame) // 3)

    result = analyse(partial, previous_snapshot=good.snapshot.to_dict())
    issue = result.quality.blocking[0]

    assert issue.title
    assert issue.detail
    assert "Analysis held" in result.quality.headline


def test_healthy_data_still_produces_its_story() -> None:
    result = analyse(fixtures.planted_business(), strict=True)

    assert not result.held
    assert result.findings
    assert result.quality is not None and result.quality.passed


def test_the_gate_can_be_skipped_deliberately() -> None:
    frame = fixtures.planted_business()
    doubled = pd.concat([frame, frame], ignore_index=True)

    held = analyse(doubled)
    forced = analyse(doubled, skip_quality_gate=True)

    assert held.held and held.findings == []
    assert forced.findings, "an explicit override still runs"


def test_snapshot_round_trips_through_json() -> None:
    frame = fixtures.planted_business()
    snapshot = check(frame, _assignments(frame)).snapshot
    restored = Snapshot.from_dict(snapshot.to_dict())

    assert restored.rows == snapshot.rows
    assert restored.median_value == pytest.approx(snapshot.median_value)
