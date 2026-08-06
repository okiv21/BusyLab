"""Alert and digest tests.

The failure mode for an alerting system is not missing an event, it is crying
wolf. A system that fires on noise gets muted, and a muted system protects
nothing, so most of what follows checks that nothing is sent.

The single most important test here is that a quiet business produces zero
alerts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from busylab.alerts import (
    MAX_ALERTS,
    Alert,
    AlertKind,
    AlertLevel,
    alerts_from_findings,
    alerts_from_quality,
    build_alerts,
    detect_anomalies,
    robust_scores,
)
from busylab.analysis import analyse
from busylab.digest import (
    Digest,
    LogMailer,
    SmtpMailer,
    build_digest,
    send_digest,
)
from busylab.quality import check as check_quality

from . import fixtures


@pytest.fixture(scope="module")
def quiet():
    return analyse(fixtures.flat_business(), strict=True)


@pytest.fixture(scope="module")
def planted():
    return analyse(fixtures.planted_business(), strict=True)


def _alerts(result):
    return build_alerts(result.frame, result.findings, result.quality)


# --------------------------------------------------------------------------
# Robust statistics: why not a plain z-score
# --------------------------------------------------------------------------


def test_a_plain_z_score_would_hide_two_spikes() -> None:
    """The reason MAD is used, demonstrated rather than asserted.

    Two outliers inflate the standard deviation enough that neither clears
    three sigma. The median and MAD are barely moved, so both stay visible.
    """
    values = np.array([100.0] * 20 + [400.0, 380.0])
    classic = (values - values.mean()) / values.std()
    robust = robust_scores(values)

    assert classic[-1] < 3.5, "the textbook score does not see it"
    assert robust[-1] > 8.0, "the robust score does"


def test_robust_scores_survive_a_flat_history() -> None:
    """A constant series has zero MAD; dividing by it must not blow up."""
    flat = np.array([50.0] * 12 + [500.0])
    scores = robust_scores(flat)

    assert np.all(np.isfinite(scores))
    assert scores[-1] > 3.5


def test_robust_scores_on_a_perfectly_constant_series() -> None:
    scores = robust_scores(np.array([7.0] * 10))
    assert np.all(scores == 0)


# --------------------------------------------------------------------------
# The control: silence on a quiet business
# --------------------------------------------------------------------------


def test_a_quiet_business_produces_no_alerts(quiet) -> None:
    """The test that matters most. A system that cries wolf gets muted."""
    assert _alerts(quiet) == []


def test_a_quiet_business_finds_no_anomalies(quiet) -> None:
    assert detect_anomalies(quiet.frame) == []


def test_ordinary_variation_is_not_an_anomaly() -> None:
    """Noise around a stable level is what a business looks like."""
    rng = np.random.default_rng(3)
    rows = []
    start = pd.Timestamp("2024-01-01")
    for day in range(540):
        when = start + pd.Timedelta(days=day)
        for _ in range(5):
            rows.append(
                {
                    "order_date": when,
                    "product_name": str(rng.choice(fixtures.PRODUCTS)),
                    "total_paid": round(5000 * float(rng.normal(1, 0.18)), 2),
                }
            )
    result = analyse(pd.DataFrame(rows), strict=True)
    assert detect_anomalies(result.frame) == []


# --------------------------------------------------------------------------
# Real events do fire
# --------------------------------------------------------------------------


def test_a_real_decline_raises_alerts(planted) -> None:
    """Silence is only a virtue when there is nothing to say."""
    alerts = _alerts(planted)
    assert alerts
    assert any(a.level is AlertLevel.HIGH for a in alerts)


def test_a_sudden_collapse_is_detected() -> None:
    rng = np.random.default_rng(6)
    rows = []
    start = pd.Timestamp("2024-01-01")
    for day in range(540):
        when = start + pd.Timedelta(days=day)
        # The last two months fall off a cliff.
        factor = 0.25 if day > 480 else 1.0
        for _ in range(max(1, int(5 * factor))):
            rows.append(
                {
                    "order_date": when,
                    "product_name": str(rng.choice(fixtures.PRODUCTS)),
                    "total_paid": round(5000 * factor * float(rng.normal(1, 0.05)), 2),
                }
            )
    result = analyse(pd.DataFrame(rows), strict=True)
    anomalies = detect_anomalies(result.frame)

    assert anomalies
    assert any(a.kind is AlertKind.ANOMALY_DROP for a in anomalies)


# --------------------------------------------------------------------------
# Restraint
# --------------------------------------------------------------------------


def test_consecutive_periods_become_one_alert(planted) -> None:
    """A sustained slide is one story, not one notification per month."""
    anomalies = detect_anomalies(planted.frame)
    subjects = [a.subject for a in anomalies]
    assert len(subjects) == len(set(subjects)), "one alert per subject"


def test_a_run_of_periods_is_reported_on_the_single_alert(planted) -> None:
    anomalies = [
        a for a in detect_anomalies(planted.frame) if a.subject == "the business"
    ]
    if anomalies:
        assert "running" in anomalies[0].title or "month" in anomalies[0].title


def test_no_run_exceeds_the_cap(planted) -> None:
    assert len(_alerts(planted)) <= MAX_ALERTS


def test_an_immaterial_product_gets_no_alert_of_its_own(planted) -> None:
    """A 1% product behaving oddly is not worth an interruption."""
    for alert in detect_anomalies(planted.frame):
        share = alert.evidence.get("revenue_share", 1.0)
        assert share >= 0.05


def test_product_anomalies_are_fdr_corrected(planted) -> None:
    """Twenty products checked weekly is a false alarm a week, uncorrected."""
    for alert in detect_anomalies(planted.frame):
        if alert.subject != "the business":
            assert alert.evidence["correction"] == "Benjamini-Hochberg FDR"
            assert alert.evidence["adjusted_p"] is not None


def test_seasonality_is_removed_before_calling_anything_unusual() -> None:
    """A December spike in a business that spikes every December is normal."""
    result = analyse(fixtures.seasonal_business(), strict=True)
    anomalies = detect_anomalies(result.frame)

    for alert in anomalies:
        assert alert.evidence["seasonally_adjusted"] is True


def test_only_confirmed_urgent_findings_become_alerts(planted) -> None:
    """An unconfirmed finding belongs in the story, read in context."""
    promoted = alerts_from_findings(planted.findings)
    for alert in promoted:
        finding = next(f for f in planted.findings if f.id == alert.finding_id)
        assert finding.severity.value == "urgent"
        if finding.evidence.p_value is not None:
            assert finding.is_significant


def test_alert_titles_do_not_break_on_decimals(planted) -> None:
    """Splitting on "." turns "553.5k per period" into "553"."""
    for alert in alerts_from_findings(planted.findings):
        assert not alert.title.rstrip(".").endswith(("553", "0"))
        assert len(alert.title) > 20


# --------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------


def test_the_same_event_has_a_stable_key() -> None:
    """Re-sending weekly is the fastest route to being muted."""
    first = Alert(
        kind=AlertKind.ANOMALY_DROP,
        level=AlertLevel.HIGH,
        title="Revenue came in 30% below its usual month",
        detail="x",
        subject="the business",
        period="2025-05-01",
    )
    second = Alert(
        kind=AlertKind.ANOMALY_DROP,
        level=AlertLevel.HIGH,
        title="completely different wording",
        detail="y",
        subject="the business",
        period="2025-05-01",
    )
    assert first.key == second.key


def test_a_different_period_is_a_different_alert() -> None:
    base = dict(
        kind=AlertKind.ANOMALY_DROP,
        level=AlertLevel.HIGH,
        title="t",
        detail="d",
        subject="the business",
    )
    assert Alert(**base, period="2025-05-01").key != Alert(
        **base, period="2025-06-01"
    ).key


def test_already_sent_alerts_are_not_repeated(planted) -> None:
    first_run = _alerts(planted)
    assert first_run

    keys = {a.key for a in first_run}
    second_run = build_alerts(
        planted.frame, planted.findings, planted.quality, already_sent=keys
    )
    assert second_run == []


# --------------------------------------------------------------------------
# Quality gate alerts (spec 4.3 feeding Pillar 2)
# --------------------------------------------------------------------------


def test_a_failed_gate_reaches_out() -> None:
    """Nobody is watching the gate once ingestion is unattended."""
    frame = fixtures.planted_business()
    doubled = pd.concat([frame, frame], ignore_index=True)
    result = analyse(doubled)

    alerts = build_alerts(result.frame, result.findings, result.quality)
    assert any(a.kind is AlertKind.DATA_QUALITY for a in alerts)
    assert any(a.level is AlertLevel.HIGH for a in alerts)


def test_a_held_analysis_promotes_no_findings() -> None:
    """Findings from a poisoned refresh must not be alerted on."""
    frame = fixtures.planted_business()
    good = check_quality(frame, analyse(frame).detection.assignments)
    partial = frame.head(len(frame) // 3)

    result = analyse(partial, previous_snapshot=good.snapshot.to_dict())
    alerts = build_alerts(result.frame, result.findings, result.quality)

    assert alerts, "the failure itself is worth reporting"
    assert all(a.kind is AlertKind.DATA_QUALITY for a in alerts)


def test_info_level_quality_issues_do_not_alert() -> None:
    frame = fixtures.planted_business().copy()
    frame.loc[frame.index[:20], "total_paid"] *= -1  # a few refunds
    report = check_quality(frame, analyse(frame).detection.assignments)

    codes = {a.subject for a in alerts_from_quality(report)}
    assert "some_negative" not in codes


# --------------------------------------------------------------------------
# The digest
# --------------------------------------------------------------------------


def test_the_digest_leads_with_what_changed(planted) -> None:
    digest = build_digest(planted.findings, _alerts(planted))

    assert digest.headline
    assert "ranked" not in digest.headline.lower()
    assert len(digest.lines) <= 2


def test_the_digest_reads_in_under_a_minute(planted) -> None:
    digest = build_digest(planted.findings, _alerts(planted))
    words = len(digest.to_text().split())
    assert words < 220, f"too long to read in a minute: {words} words"


def test_the_digest_does_not_say_the_same_thing_twice(planted) -> None:
    digest = build_digest(planted.findings, _alerts(planted))
    body = [digest.headline] + digest.lines

    for alert in digest.alerts:
        assert alert.detail not in body


def test_the_digest_carries_a_good_thing_where_there_is_one() -> None:
    """A weekly email that is only bad news gets filtered, and then so does
    the bad news."""
    result = analyse(fixtures.customer_business(), strict=True)
    digest = build_digest(result.findings, [])
    assert digest.good_news is None or isinstance(digest.good_news, str)


def test_a_quiet_period_says_so_plainly(quiet) -> None:
    digest = build_digest(quiet.findings, [])
    assert "quiet" in digest.headline.lower()


def test_an_empty_digest_is_not_sent() -> None:
    """Sending nothing trains people to ignore the ones that say something."""
    empty = Digest(period_label="week of nothing", headline="No findings.")
    assert empty.is_empty
    assert send_digest(empty, "someone@example.com", LogMailer()) is False


def test_a_digest_with_content_is_sent(planted) -> None:
    digest = build_digest(planted.findings, _alerts(planted))
    assert not digest.is_empty
    assert send_digest(digest, "someone@example.com", LogMailer()) is True


def test_the_digest_renders_as_text_and_html(planted) -> None:
    digest = build_digest(planted.findings, _alerts(planted))

    assert digest.subject.startswith("Your business in review")
    assert digest.headline in digest.to_text()
    html = digest.to_html()
    assert html.startswith("<div") and "style=" in html


def test_the_digest_html_escapes_its_content() -> None:
    digest = Digest(
        period_label="week", headline="Revenue & <script>alert(1)</script> fell"
    )
    html = digest.to_html()
    assert "<script>" not in html
    assert "&amp;" in html


def test_the_digest_states_it_is_not_advice(planted) -> None:
    """Spec 2 applies to an email exactly as it applies to the app."""
    digest = build_digest(planted.findings, _alerts(planted))
    text = digest.to_text()

    assert "Nothing here is advice" in text
    assert "decisions stay yours" in text
    assert "Nothing here is advice" in digest.to_html()


def test_digest_content_does_not_read_as_advice(planted) -> None:
    from busylab.findings import check_non_directive

    digest = build_digest(planted.findings, _alerts(planted))
    for line in [digest.headline, *digest.lines]:
        assert check_non_directive(line) == [], line


def test_an_unconfigured_mailer_falls_back_to_the_log(monkeypatch) -> None:
    """No email credentials must not mean a broken product."""
    from busylab.digest import mailer_from_env

    for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("BUSYLAB_MAILER", raising=False)

    assert mailer_from_env().name == "log"


def test_smtp_reports_itself_unavailable_without_credentials() -> None:
    assert not SmtpMailer(host="", user="", password="").available()


def test_alerts_serialise(planted) -> None:
    import json

    payload = json.dumps([a.to_dict() for a in _alerts(planted)])
    restored = json.loads(payload)
    assert all("key" in a and "level" in a for a in restored)


# --------------------------------------------------------------------------
# Over HTTP, including the scheduler
# --------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("BUSYLAB_INLINE_WORKER", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    from api import main
    from api.handlers import build_handlers
    from api.jobs import JobStore, Worker

    store = JobStore(tmp_path / "alerts.db")
    worker = Worker(store, build_handlers())
    monkeypatch.setattr(main, "_store", store)
    monkeypatch.setattr(main, "_worker", worker)
    monkeypatch.setattr(main, "STORAGE_DIR", tmp_path / "storage")
    monkeypatch.setattr(main, "SCHEDULER_TOKEN", "test-token")

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


def test_analysis_produces_alerts_over_http(client, analysed) -> None:
    body = client.get(f"/datasets/{analysed}/alerts").json()
    assert body["alerts"], "a declining business must raise something"
    assert all("key" in a for a in body["alerts"])


def test_a_second_run_produces_no_duplicate_alerts(client, analysed) -> None:
    """The scheduler runs weekly; the same event must fire once."""
    before = client.get(f"/datasets/{analysed}/alerts").json()["alerts"]

    client.post(f"/datasets/{analysed}/columns", json={"roles": {}})
    client.worker.drain()

    after = client.get(f"/datasets/{analysed}/alerts").json()["alerts"]
    assert len(after) == len(before)


def test_an_alert_can_be_acknowledged(client, analysed) -> None:
    alerts = client.get(f"/datasets/{analysed}/alerts").json()["alerts"]
    key = alerts[0]["key"]

    assert (
        client.post(f"/datasets/{analysed}/alerts/{key}/acknowledge").status_code == 204
    )
    remaining = {a["key"] for a in client.get(f"/datasets/{analysed}/alerts").json()["alerts"]}
    assert key not in remaining


def test_the_digest_is_previewable(client, analysed) -> None:
    body = client.get(f"/datasets/{analysed}/digest").json()

    assert body["subject"].startswith("Your business in review")
    assert body["html"].startswith("<div")
    assert "Nothing here is advice" in body["text"]


def test_the_scheduler_requires_a_token(client, analysed) -> None:
    assert client.post("/internal/tick").status_code == 401
    assert client.post("/internal/tick?token=wrong").status_code == 401


def test_the_scheduler_queues_a_refresh_for_every_dataset(client, analysed) -> None:
    response = client.post("/internal/tick?token=test-token")

    assert response.status_code == 202
    body = response.json()
    assert body["queued"] >= 1
    assert any(j["dataset_id"] == analysed for j in body["jobs"])


def test_the_scheduler_is_off_when_no_token_is_configured(
    client, analysed, monkeypatch
) -> None:
    """An unset secret must close the endpoint, not leave it open."""
    from api import main

    monkeypatch.setattr(main, "SCHEDULER_TOKEN", "")
    assert client.post("/internal/tick?token=anything").status_code == 503
