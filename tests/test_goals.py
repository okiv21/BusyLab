"""Goal tracking tests.

Spec Pillar 4's example output sets the bar: *"At current pace you will reach 87
percent of your Q1 target, and the gap is entirely Product 4's decline."* Both
halves have to work - the projection, and the attribution.

The window can be in the past, in progress, or not yet started, and each needs
a different answer. A finished window has no projection to make; a window barely
begun has nothing worth projecting from.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from busylab.analysis import analyse, build
from busylab.analysis.goals import measure
from busylab.detection import detect
from busylab.goals import Goal

from . import fixtures


@pytest.fixture(scope="module")
def sales() -> pd.DataFrame:
    """Planted business, ending 2025-06-23."""
    return fixtures.planted_business()


@pytest.fixture(scope="module")
def frame(sales):
    return build(sales, detect(sales))


def _goal_finding(result):
    return next((f for f in result.findings if f.id.startswith("goal_")), None)


def _goal(**kwargs) -> Goal:
    defaults = dict(
        id="g1",
        metric="revenue",
        target=40_000_000.0,
        start=date(2025, 1, 1),
        end=date(2025, 9, 30),
        label="Q1-Q3 target",
    )
    defaults.update(kwargs)
    return Goal(**defaults)


# --------------------------------------------------------------------------
# The model refuses nonsense
# --------------------------------------------------------------------------


def test_a_target_must_be_positive() -> None:
    with pytest.raises(ValueError):
        _goal(target=0)


def test_a_goal_cannot_end_before_it_starts() -> None:
    with pytest.raises(ValueError):
        _goal(start=date(2025, 6, 1), end=date(2025, 1, 1))


def test_only_known_metrics_are_accepted() -> None:
    with pytest.raises(ValueError):
        _goal(metric="vibes")


def test_a_goal_round_trips_through_json() -> None:
    goal = _goal()
    restored = Goal.from_dict(goal.to_dict())
    assert restored.target == goal.target
    assert restored.start == goal.start


# --------------------------------------------------------------------------
# The three states of a window
# --------------------------------------------------------------------------


def test_a_future_window_says_nothing_yet(frame) -> None:
    progress = measure(frame, _goal(start=date(2030, 1, 1), end=date(2030, 12, 31)))
    assert progress is not None
    assert progress.state == "not_started"


def test_a_closed_window_reports_the_actual_not_a_projection(frame) -> None:
    goal = _goal(target=30_000_000.0, start=date(2024, 1, 1), end=date(2024, 12, 31))
    progress = measure(frame, goal)

    assert progress.state == "finished"
    assert progress.elapsed == 1.0
    assert progress.projected == progress.actual
    assert progress.projected_low == progress.projected_high
    assert "actual total" in progress.method


def test_an_in_progress_window_is_projected_with_a_band(frame) -> None:
    progress = measure(frame, _goal())

    assert progress.state == "in_progress"
    assert 0 < progress.elapsed < 1
    assert progress.projected_low <= progress.projected <= progress.projected_high
    assert progress.projected > progress.actual, "there is still time left to run"


def test_now_is_the_last_sale_not_today(frame) -> None:
    """A file uploaded late must not read as weeks of zero sales."""
    progress = measure(frame, _goal())
    assert progress.as_of == "2025-06-23"


# --------------------------------------------------------------------------
# On track, behind, and the difference between might and will
# --------------------------------------------------------------------------


def test_a_comfortable_target_reads_as_on_track(sales) -> None:
    goal = _goal(id="easy", target=8_000_000.0)
    result = analyse(sales, strict=True, goals=[goal])
    finding = _goal_finding(result)

    assert finding is not None
    assert finding.facts["on_track"] is True
    assert finding.severity.value == "good"


def test_an_unreachable_target_reads_as_urgent(sales) -> None:
    goal = _goal(id="hard", target=200_000_000.0)
    result = analyse(sales, strict=True, goals=[goal])
    finding = _goal_finding(result)

    assert finding is not None
    assert finding.facts["on_track"] is False
    assert finding.facts["could_still_hit"] is False
    assert finding.severity.value == "urgent"


def test_might_miss_is_distinguished_from_will_miss(frame) -> None:
    """A single projected number cannot express the difference.

    Target placed just above the central projection but inside the upper band,
    so the honest answer is "not out of reach" rather than a flat miss.
    """
    baseline = measure(frame, _goal(target=1.0))
    midpoint = (baseline.projected + baseline.projected_high) / 2
    if midpoint <= baseline.projected:
        pytest.skip("band too narrow on this fixture to construct the case")

    progress = measure(frame, _goal(target=midpoint))
    assert progress.on_track is False
    assert progress.could_still_hit is True


def test_share_of_target_is_reported(sales) -> None:
    """Spec's phrasing: "you will reach 87 percent of your target"."""
    result = analyse(sales, strict=True, goals=[_goal()])
    finding = _goal_finding(result)

    assert 0 < finding.facts["share_of_target"] < 1
    assert "% of its" in finding.summary


# --------------------------------------------------------------------------
# Attribution: where the gap lives
# --------------------------------------------------------------------------


def test_a_shortfall_is_attributed_to_products(sales) -> None:
    result = analyse(sales, strict=True, goals=[_goal(target=200_000_000.0)])
    finding = _goal_finding(result)

    drivers = finding.facts["gap_drivers"]
    assert drivers, "a gap this large must be explained"
    assert all(d["change"] < 0 for d in drivers), "only decliners explain a gap"
    assert drivers[0]["product"] in finding.summary


def test_a_surplus_needs_no_attribution(sales) -> None:
    result = analyse(sales, strict=True, goals=[_goal(id="easy", target=8_000_000.0)])
    finding = _goal_finding(result)
    assert finding.facts["gap_drivers"] == []


# --------------------------------------------------------------------------
# Metric availability and house rules
# --------------------------------------------------------------------------


def test_a_profit_goal_needs_a_cost_column(sales) -> None:
    without_cost = sales.drop(columns=["unit_cost"])
    result = analyse(without_cost, strict=True, goals=[_goal(metric="profit")])
    assert _goal_finding(result) is None


def test_a_profit_goal_works_when_cost_is_present(sales) -> None:
    result = analyse(
        sales, strict=True, goals=[_goal(metric="profit", target=5_000_000.0)]
    )
    finding = _goal_finding(result)
    assert finding is not None
    assert finding.facts["goal"]["metric"] == "profit"


def test_goal_findings_carry_the_progress_arc(sales) -> None:
    result = analyse(sales, strict=True, goals=[_goal()])
    finding = _goal_finding(result)

    assert finding.chart.value == "progress_arc"
    assert finding.chart_data["target"] > 0
    assert "pace" in finding.chart_data


def test_goal_findings_do_not_read_as_advice(sales) -> None:
    from busylab.findings import check_non_directive

    for target in (8_000_000.0, 40_000_000.0, 200_000_000.0):
        result = analyse(sales, strict=True, goals=[_goal(target=target)])
        finding = _goal_finding(result)
        assert check_non_directive(finding.summary) == [], finding.summary


def test_several_goals_are_tracked_independently(sales) -> None:
    goals = [
        _goal(id="a", target=8_000_000.0, label="Modest"),
        _goal(id="b", target=200_000_000.0, label="Stretch"),
    ]
    result = analyse(sales, strict=True, goals=goals)
    found = [f for f in result.findings if f.id.startswith("goal_")]

    assert {f.id for f in found} == {"goal_a", "goal_b"}


def test_no_goals_means_no_goal_findings(sales) -> None:
    result = analyse(sales, strict=True)
    assert _goal_finding(result) is None


# --------------------------------------------------------------------------
# Over HTTP
# --------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BUSYLAB_INLINE_WORKER", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    from api import main
    from api.handlers import build_handlers
    from api.jobs import JobStore, Worker
    from api.storage import LocalFileStore

    files = LocalFileStore(root=tmp_path / "storage")
    store = JobStore(tmp_path / "goals.db")
    worker = Worker(store, build_handlers(files))
    monkeypatch.setattr(main, "_store", store)
    monkeypatch.setattr(main, "_worker", worker)
    monkeypatch.setattr(main, "FILES", files)

    with TestClient(main.app) as test_client:
        test_client.worker = worker
        test_client.store = store
        yield test_client


@pytest.fixture
def analysed(client, tmp_path):
    path = tmp_path / "sales.xlsx"
    fixtures.planted_business().drop(columns=["salesperson"]).to_excel(
        path, index=False
    )
    with open(path, "rb") as handle:
        upload = client.post(
            "/uploads", files={"file": (path.name, handle, "application/vnd.ms-excel")}
        ).json()
    client.worker.drain()
    client.post(f"/datasets/{upload['dataset_id']}/columns", json={"roles": {}})
    client.worker.drain()
    return upload["dataset_id"]


def test_setting_a_target_puts_it_in_the_story(client, analysed) -> None:
    response = client.post(
        f"/datasets/{analysed}/goals",
        json={
            "metric": "revenue",
            "target": 40_000_000,
            "start": "2025-01-01",
            "end": "2025-09-30",
            "label": "Q1-Q3",
        },
    )
    assert response.status_code == 201
    client.worker.drain()

    story = client.get(f"/datasets/{analysed}/story").json()
    goal_findings = [f for f in story["findings"] if f["id"].startswith("goal_")]

    assert goal_findings, "the story must include the target once it is set"
    assert goal_findings[0]["chart"] == "progress_arc"


def test_goals_can_be_listed_and_removed(client, analysed) -> None:
    created = client.post(
        f"/datasets/{analysed}/goals",
        json={
            "metric": "revenue",
            "target": 1_000_000,
            "start": "2025-01-01",
            "end": "2025-12-31",
        },
    ).json()
    goal_id = created["goal"]["id"]

    assert any(g["id"] == goal_id for g in client.get(f"/datasets/{analysed}/goals").json()["goals"])

    assert client.delete(f"/datasets/{analysed}/goals/{goal_id}").status_code == 202
    assert client.get(f"/datasets/{analysed}/goals").json()["goals"] == []


def test_the_api_rejects_a_goal_the_engine_would_reject(client, analysed) -> None:
    """Validation lives in the engine, not duplicated in the API."""
    bad = client.post(
        f"/datasets/{analysed}/goals",
        json={
            "metric": "revenue",
            "target": 1000,
            "start": "2025-12-31",
            "end": "2025-01-01",
        },
    )
    assert bad.status_code == 422


def test_an_unknown_metric_is_rejected(client, analysed) -> None:
    response = client.post(
        f"/datasets/{analysed}/goals",
        json={
            "metric": "vibes",
            "target": 1000,
            "start": "2025-01-01",
            "end": "2025-12-31",
        },
    )
    assert response.status_code == 422


def test_goals_on_a_missing_dataset_are_a_404(client) -> None:
    assert client.get("/datasets/nope/goals").status_code == 404
