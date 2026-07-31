"""Analysis engine tests.

These are written against *planted ground truth* rather than against whatever
the engine happens to output. ``fixtures.planted_business`` contains known
effects that must be found; ``fixtures.flat_business`` contains none and is the
control that catches an engine tuned to always have something to say.

The two hardest rules in the spec are asserted mechanically here: no finding
may read as advice (spec 2), and no finding may survive multiple comparisons it
should not have (spec 3.3).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from busylab.analysis import analyse
from busylab.analysis.dataset import PROFIT, REVENUE
from busylab.analysis.stats import benjamini_hochberg, concentration, trend_test
from busylab.findings import ChartType, FindingType, Severity

from . import fixtures


@pytest.fixture(scope="module")
def planted():
    return analyse(fixtures.planted_business(), strict=True)


@pytest.fixture(scope="module")
def flat():
    return analyse(fixtures.flat_business(), strict=True)


def _ids(result) -> set[str]:
    return {f.id for f in result.findings}


def _by_id(result, finding_id: str):
    return next(f for f in result.findings if f.id == finding_id)


# --------------------------------------------------------------------------
# The planted truths must be found
# --------------------------------------------------------------------------


def test_engine_runs_without_errors(planted) -> None:
    assert planted.errors == []
    assert planted.findings


def test_finds_the_planted_decline(planted) -> None:
    trend = _by_id(planted, "revenue_trend")
    assert trend.type is FindingType.TREND
    assert trend.facts["direction"] == "down"
    assert trend.is_significant


def test_finds_margin_reality(planted) -> None:
    """Best seller is not best earner: the headline insight of spec 5."""
    finding = _by_id(planted, "margin_reality")
    assert finding.facts["top_seller"] == "Linen Candle"
    assert finding.facts["top_earner"] == "Ceramic Diffuser"
    assert finding.facts["profit_rank_of_top_seller"] > 1


def test_finds_the_loss_making_product(planted) -> None:
    finding = _by_id(planted, "loss_making_product")
    assert finding.facts["product"] == "Gift Box"
    assert finding.facts["margin"] < 0
    assert finding.severity is Severity.URGENT


def test_finds_profit_concentration(planted) -> None:
    finding = _by_id(planted, "concentration")
    assert finding.facts["metric"] == "profit"
    assert finding.facts["leader"] == "Ceramic Diffuser"
    assert finding.facts["top1_share"] > 0.4


def test_locates_the_decline_in_the_right_channel(planted) -> None:
    """Spec 3.3's "dying online, fine in store"."""
    finding = _by_id(planted, "decomposition_channel")
    assert finding.facts["driver"] == "online"
    assert finding.facts["driver_share_of_change"] > 0.6
    assert "in store" in finding.facts["steady_levels"]


def test_identifies_volume_not_price_as_the_cause(planted) -> None:
    """Orders were removed, prices were untouched. The split must say so."""
    finding = _by_id(planted, "price_volume_split")
    assert finding.facts["dominant"] == "volume"
    assert abs(finding.facts["price_change_pct"]) < 0.1


def test_decomposition_contributions_sum_to_the_total(planted) -> None:
    finding = _by_id(planted, "revenue_decomposition")
    parts = sum(c["change"] for c in finding.facts["contributions"])
    assert parts == pytest.approx(finding.facts["total_change"], rel=1e-6)


# --------------------------------------------------------------------------
# The control: nothing must be invented
# --------------------------------------------------------------------------


def test_flat_business_yields_no_significant_findings(flat) -> None:
    """The anti-garbage control (spec 3.3)."""
    assert flat.significant == []


def test_flat_business_reports_noise_as_noise(flat) -> None:
    trend = _by_id(flat, "revenue_trend")
    assert trend.type is FindingType.NOISE
    assert not trend.is_significant


def test_flat_business_invents_no_concentration(flat) -> None:
    assert "concentration" not in _ids(flat)


def test_flat_business_invents_no_segmentation(flat) -> None:
    assert not any(f.id.startswith("segmentation_") for f in flat.findings)


def test_flat_business_invents_no_channel_story(flat) -> None:
    assert not any(f.id.startswith("decomposition_") for f in flat.findings)


def test_unrelated_dimension_produces_no_finding(planted) -> None:
    """Salesperson is planted as pure noise and must stay silent."""
    assert not any("salesperson" in f.id for f in planted.findings)


def test_random_noise_survives_multiple_comparison_correction() -> None:
    """200 tests on noise: naive testing finds ~10, FDR must find ~0."""
    rng = np.random.default_rng(0)
    p_values = rng.uniform(0, 1, 200).tolist()
    naive = sum(p < 0.05 for p in p_values)
    rejected, adjusted = benjamini_hochberg(p_values)

    assert naive >= 5, "sanity: uncorrected testing does produce false positives"
    assert sum(rejected) == 0
    assert max(adjusted) <= 1.0


def test_correction_still_lets_real_findings_through() -> None:
    """Bonferroni would suppress these; FDR is chosen so it does not."""
    p_values = [1e-6] * 20 + np.random.default_rng(1).uniform(0, 1, 180).tolist()
    rejected, _ = benjamini_hochberg(p_values)
    assert sum(rejected) >= 20


def test_a_group_gap_caused_by_product_mix_is_not_claimed() -> None:
    """A group selling pricier products is not a group that sells better.

    Salesperson is assigned at random in the fixture, so any gap in average
    order value is composition: whoever happened to sell more Ceramic Diffuser
    looks better. Reporting that as a real difference is the impressive-looking
    garbage spec 3.3 warns about, so the confound has to be named.
    """
    from busylab.detection import detect
    from busylab.roles import Role

    frame = fixtures.planted_business()
    detection = detect(frame, overrides={"salesperson": Role.GROUP_BY})
    result = analyse(frame, detection, strict=True)

    segments = [f for f in result.findings if f.id.startswith("segmentation_")]
    for finding in segments:
        if "salesperson" in finding.id:
            assert finding.facts["confirmed"] is False
            assert finding.facts["mix_confounded"] is True
            assert "mix" in finding.summary.lower()
            assert finding.importance < 0.5, "a confounded gap must not lead"


def test_segmentation_reports_correction_it_applied(planted) -> None:
    for finding in planted.findings:
        if finding.id.startswith("segmentation_"):
            assert finding.evidence.correction == "Benjamini-Hochberg FDR"
            assert finding.evidence.adjusted_p is not None


# --------------------------------------------------------------------------
# Seasonality must not be mistaken for decline
# --------------------------------------------------------------------------


def test_normal_january_dip_is_not_reported_as_a_decline() -> None:
    """Spec 5: deseasonalise so a normal December dip is not flagged."""
    result = analyse(fixtures.seasonal_business(), strict=True)
    trend = _by_id(result, "revenue_trend")

    assert trend.facts["seasonally_adjusted"] is True
    assert trend.type is FindingType.NOISE, (
        "underlying demand is flat; only the calendar moves"
    )


def test_statistically_detectable_but_trivial_drift_is_not_a_trend() -> None:
    """A big sample makes tiny drifts detectable; they are still not news.

    Reporting a 2% drift as a trend because 3,000 rows made it significant is
    the same class of error as reporting an uncorrected multiple comparison.
    """
    rng = np.random.default_rng(4)
    rows = []
    start = pd.Timestamp("2024-01-01")
    for day in range(600):
        when = start + pd.Timedelta(days=day)
        level = 5000.0 * (1 - 0.00003 * day)  # ~2% over the whole period
        for _ in range(6):
            rows.append(
                {
                    "order_date": when,
                    "product_name": str(rng.choice(fixtures.PRODUCTS)),
                    "total_paid": round(level * float(rng.normal(1, 0.02)), 2),
                }
            )
    result = analyse(pd.DataFrame(rows), strict=True)
    trend = _by_id(result, "revenue_trend")

    assert trend.type is FindingType.NOISE
    assert trend.facts["material"] is False


def test_seasonal_shape_is_reported_on_its_own_terms() -> None:
    result = analyse(fixtures.seasonal_business(), strict=True)
    seasonal = _by_id(result, "seasonality")
    assert seasonal.facts["peak_month_name"] == "December"
    assert seasonal.facts["peak_lift"] > 0.3


# --------------------------------------------------------------------------
# The non-negotiable: insights, not directives (spec 2)
# --------------------------------------------------------------------------


def test_no_finding_tells_the_owner_what_to_do(planted, flat) -> None:
    from busylab.findings import check_non_directive

    for result in (planted, flat):
        for finding in result.findings:
            problems = check_non_directive(finding.summary)
            assert not problems, f"{finding.id}: {finding.summary!r} {problems}"


@pytest.mark.parametrize(
    "text",
    [
        "You should raise the price of the candle.",
        "We recommend dropping Gift Box.",
        "Consider removing this product.",
        "Bundle idea: candle plus matches.",
    ],
)
def test_directive_language_is_actually_detected(text: str) -> None:
    """The guard is only worth having if it catches real phrasing."""
    from busylab.findings import check_non_directive

    assert check_non_directive(text)


@pytest.mark.parametrize(
    "text",
    [
        "Revenue is down 18% since March, and it is not seasonal.",
        "68% of profit comes from one product.",
        "Linen Candle brings in the most revenue but is your third most profitable product.",
    ],
)
def test_factual_statements_pass_the_guard(text: str) -> None:
    from busylab.findings import check_non_directive

    assert check_non_directive(text) == []


# --------------------------------------------------------------------------
# Contract: charts, ranking, serialisation
# --------------------------------------------------------------------------


def test_every_finding_carries_a_chart(planted) -> None:
    for finding in planted.findings:
        assert finding.chart is not None


def test_chart_follows_from_finding_type(planted) -> None:
    """Spec 7: chart selection is a deterministic mapping, not a choice."""
    expected = {
        FindingType.TREND: ChartType.LINE_WITH_BAND,
        FindingType.NOISE: ChartType.LINE_WITH_BAND,
        FindingType.RANKING: ChartType.BAR_HORIZONTAL,
        FindingType.TENSION: ChartType.SCATTER,
        FindingType.DECOMPOSITION: ChartType.WATERFALL,
        FindingType.RELATIONSHIP: ChartType.CORRELATION_HEATMAP,
    }
    for finding in planted.findings:
        if finding.type in expected and finding.type is not FindingType.CONCENTRATION:
            assert finding.chart is expected[finding.type]


def test_concentration_switches_to_treemap_when_crowded() -> None:
    from busylab.findings import chart_for

    assert chart_for(FindingType.CONCENTRATION, 3) is ChartType.DONUT
    assert chart_for(FindingType.CONCENTRATION, 9) is ChartType.TREEMAP


def test_story_leads_with_what_matters(planted) -> None:
    """Ranked narrative, most important first (spec 6)."""
    top_three = [f.id for f in planted.findings[:3]]
    assert "product_ranking" not in top_three, "they packed the boxes"
    assert planted.headline.severity in (Severity.URGENT, Severity.WATCH)


def test_commodity_ranking_sits_at_the_bottom(planted) -> None:
    assert planted.findings[-1].id == "product_ranking"


def test_findings_serialise_to_json(planted) -> None:
    payload = json.dumps(planted.to_dict())
    assert len(payload) > 100
    restored = json.loads(payload)
    assert restored["findings"][0]["chart"]
    assert "strength" in restored["findings"][0]["evidence"]


def test_every_finding_carries_its_evidence(planted) -> None:
    for finding in planted.findings:
        assert finding.evidence.method, f"{finding.id} has no stated method"


# --------------------------------------------------------------------------
# Tiers and graceful degradation
# --------------------------------------------------------------------------


def test_without_cost_no_profit_findings_are_claimed() -> None:
    frame = fixtures.planted_business().drop(columns=["unit_cost"])
    result = analyse(frame, strict=True)
    assert "margin_reality" not in _ids(result)
    assert "loss_making_product" not in _ids(result)
    assert _by_id(result, "concentration").facts["metric"] == "revenue"


def test_locked_tiers_are_reported_with_their_prompt() -> None:
    frame = fixtures.planted_business().drop(columns=["unit_cost", "customer_id"])
    result = analyse(frame, strict=True)
    locked = dict(result.locked())
    assert any("cost" in prompt.lower() for prompt in locked.values())


def test_short_history_does_not_produce_statistical_claims() -> None:
    """Six points is not enough to test a trend, so none is asserted."""
    frame = fixtures.planted_business().head(40)
    result = analyse(frame, strict=True)
    for finding in result.findings:
        if finding.evidence.p_value is not None:
            assert finding.evidence.sample_size >= 6


def test_missing_required_column_fails_cleanly() -> None:
    frame = fixtures.planted_business().drop(
        columns=["total_paid", "unit_price", "quantity"]
    )
    result = analyse(frame)
    assert result.errors
    assert result.findings == []


# --------------------------------------------------------------------------
# Statistics primitives
# --------------------------------------------------------------------------


def test_trend_test_detects_a_real_slope() -> None:
    series = pd.Series(np.arange(24, dtype=float) * 3 + 100)
    result = trend_test(series)
    assert result is not None and result.significant and result.statistic > 0


def test_trend_test_stays_quiet_on_noise() -> None:
    rng = np.random.default_rng(5)
    series = pd.Series(rng.normal(100, 10, 36))
    result = trend_test(series)
    assert result is not None and not result.significant


def test_trend_test_refuses_too_few_points() -> None:
    assert trend_test(pd.Series([1.0, 2.0, 3.0])) is None


def test_concentration_measures_what_it_claims() -> None:
    even = concentration(pd.Series([10.0] * 10))
    skewed = concentration(pd.Series([90.0, 5.0, 3.0, 2.0]))
    assert even["top1_share"] == pytest.approx(0.1)
    assert skewed["top1_share"] == pytest.approx(0.9)
    assert skewed["herfindahl"] > even["herfindahl"]
    assert even["items_for_half"] == 5
