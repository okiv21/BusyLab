"""Narration and routing tests.

No network. A scripted fake provider stands in for the model so the guardrails
can be tested against output the model might actually produce, including the
output it must never be allowed to publish.

The rule under test throughout: the model words things, the engine computes
things (spec 2, 8). A sentence containing a number the engine did not calculate
is a bug regardless of how well it reads.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest

from busylab.analysis import analyse
from busylab.findings import Evidence, Finding, FindingType, Severity
from busylab.narration import (
    NullProvider,
    Route,
    allowed_numbers,
    answer_from_findings,
    available_routes,
    invented_numbers,
    narrate,
    narrate_all,
    route_question,
    suggest_chips,
    verify,
)
from busylab.narration.provider import ProviderError, from_env

from . import fixtures


@dataclass
class FakeProvider:
    """A model that says exactly what the test tells it to."""

    reply: str = "Revenue is down 18% since March."
    name: str = "fake"
    is_available: bool = True
    calls: list[tuple[str, str]] = field(default_factory=list)
    fail: bool = False

    def available(self) -> bool:
        return self.is_available

    def complete(self, system: str, user: str, **kwargs) -> str:
        self.calls.append((system, user))
        if self.fail:
            raise ProviderError("simulated outage")
        return self.reply


@pytest.fixture
def finding() -> Finding:
    return Finding(
        id="revenue_trend",
        type=FindingType.TREND,
        summary="Revenue is down 18% across the period.",
        facts={"direction": "down", "change_pct": -0.18, "periods": 12},
        evidence=Evidence(method="least squares trend", p_value=0.001, sample_size=12),
        severity=Severity.URGENT,
    )


@pytest.fixture(scope="module")
def story():
    return analyse(fixtures.planted_business(), strict=True)


# --------------------------------------------------------------------------
# The engine works with no model at all
# --------------------------------------------------------------------------


def test_no_model_still_produces_a_sentence(finding) -> None:
    result = narrate(finding, NullProvider())
    assert result.text == finding.summary
    assert result.source == "engine"


def test_missing_api_key_yields_the_null_provider(monkeypatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("BUSYLAB_LLM_PROVIDER", raising=False)
    assert not from_env().available()


def test_provider_can_be_turned_off_explicitly(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "sk-whatever")
    monkeypatch.setenv("BUSYLAB_LLM_PROVIDER", "none")
    assert not from_env().available()


def test_dotenv_supplies_the_key(tmp_path, monkeypatch) -> None:
    from busylab.narration.provider import load_dotenv

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    (tmp_path / ".env").write_text('GROQ_API_KEY="from-file"\n# a comment\n')

    load_dotenv(tmp_path)
    assert os.environ["GROQ_API_KEY"] == "from-file"


def test_a_real_environment_variable_beats_the_file(tmp_path, monkeypatch) -> None:
    """A stale file must never override what the host actually set."""
    from busylab.narration.provider import load_dotenv

    monkeypatch.setenv("GROQ_API_KEY", "from-shell")
    (tmp_path / ".env").write_text("GROQ_API_KEY=from-file\n")

    load_dotenv(tmp_path)
    assert os.environ["GROQ_API_KEY"] == "from-shell"


def test_an_empty_key_is_treated_as_no_key(tmp_path, monkeypatch) -> None:
    """A .env copied from the example but not filled in must not half-work."""
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.delenv("BUSYLAB_LLM_PROVIDER", raising=False)
    assert not from_env().available()


def test_model_outage_falls_back_silently(finding) -> None:
    result = narrate(finding, FakeProvider(fail=True))
    assert result.text == finding.summary
    assert result.source == "engine"


# --------------------------------------------------------------------------
# The model may not invent numbers
# --------------------------------------------------------------------------


def test_a_number_from_the_facts_is_allowed(finding) -> None:
    assert invented_numbers("Revenue fell 18% over 12 periods.", finding) == []


def test_a_number_not_in_the_facts_is_caught(finding) -> None:
    """The core failure this architecture exists to prevent."""
    assert invented_numbers("Revenue fell 23% since March.", finding) == ["23"]


def test_hallucinated_number_is_rejected_and_replaced(finding) -> None:
    provider = FakeProvider(reply="Revenue collapsed by 47% this quarter.")
    result = narrate(finding, provider)

    assert result.source == "engine"
    assert result.text == finding.summary
    assert any("invented" in r for r in result.rejected)


def test_good_rewording_is_accepted(finding) -> None:
    provider = FakeProvider(reply="Revenue has fallen 18% over the period.")
    result = narrate(finding, provider)

    assert result.source == "model"
    assert result.text == "Revenue has fallen 18% over the period."


def test_percentages_may_be_written_unscaled(finding) -> None:
    """Facts hold 0.18; prose says 18. Both must be permitted."""
    permitted = allowed_numbers(finding)
    assert "18" in permitted
    assert "0.18" in permitted


def test_compact_money_forms_are_permitted() -> None:
    finding = Finding(
        id="x",
        type=FindingType.DECOMPOSITION,
        summary="",
        facts={"total_change": -551500.0},
    )
    permitted = allowed_numbers(finding)
    assert "551.5" in permitted  # 551.5k
    assert "551500" in permitted


def test_model_advice_is_rejected(finding) -> None:
    """Spec 2 applies to model output exactly as it applies to the engine's."""
    provider = FakeProvider(reply="Revenue is down 18%, so you should cut the price.")
    result = narrate(finding, provider)

    assert result.source == "engine"
    assert result.rejected


def test_rambling_output_is_rejected(finding) -> None:
    provider = FakeProvider(reply="Revenue is down 18%. " + "and so on " * 40)
    result = narrate(finding, provider)
    assert result.source == "engine"


def test_verify_accepts_a_clean_sentence(finding) -> None:
    assert verify("Revenue is down 18% over 12 periods.", finding) == []


# --------------------------------------------------------------------------
# Caching (spec 9: the sentence does not change until the numbers do)
# --------------------------------------------------------------------------


def test_narration_is_cached_per_finding(finding) -> None:
    provider = FakeProvider(reply="Revenue has fallen 18% over the period.")
    cache: dict[str, str] = {}

    narrate(finding, provider, cache=cache)
    narrate(finding, provider, cache=cache)

    assert len(provider.calls) == 1, "second call must be served from cache"


def test_cache_is_invalidated_when_the_numbers_change(finding) -> None:
    provider = FakeProvider(reply="Revenue has fallen 18% over the period.")
    cache: dict[str, str] = {}
    narrate(finding, provider, cache=cache)

    moved = Finding(
        id=finding.id,
        type=finding.type,
        summary=finding.summary,
        facts={**finding.facts, "change_pct": -0.31},
    )
    narrate(moved, provider, cache=cache)

    assert len(provider.calls) == 2, "different numbers must not reuse a sentence"


def test_narrating_a_story_sends_one_call_per_finding(story) -> None:
    """Small payloads suit the free tier's tokens-per-minute limit."""
    provider = FakeProvider(reply="A perfectly ordinary sentence.")
    results = narrate_all(story.findings, provider)

    assert len(provider.calls) == len(story.findings)
    assert len(results) == len(story.findings)


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def test_keywords_route_without_any_model(story) -> None:
    decision = route_question(
        "why did revenue drop?", story.findings, NullProvider()
    )
    assert decision.answerable
    assert decision.source == "keywords"
    assert decision.route.name == "why_change"


@pytest.mark.parametrize(
    "question,expected",
    [
        ("what about online vs in store?", "break_down_by_channel"),
        ("was it price or volume?", "price_or_volume"),
        ("which products actually make money?", "most_profitable"),
        ("show me week by week", "week_by_week"),
        ("what sells together?", "what_sells_together"),
    ],
)
def test_common_questions_reach_the_right_analysis(
    story, question: str, expected: str
) -> None:
    decision = route_question(question, story.findings, NullProvider())
    assert decision.route is not None and decision.route.name == expected


def test_model_choice_is_used_when_available(story) -> None:
    provider = FakeProvider(reply='{"route": "concentration", "confidence": 0.9}')
    decision = route_question("am I too reliant on one thing?", story.findings, provider)

    assert decision.source == "model"
    assert decision.route.name == "concentration"
    assert decision.confidence == pytest.approx(0.9)


def test_model_may_not_invent_an_analysis(story) -> None:
    """A route not on the list is a hallucinated capability."""
    provider = FakeProvider(reply='{"route": "predict_lottery", "confidence": 0.99}')
    decision = route_question("what wins the lottery?", story.findings, provider)

    assert decision.source != "model" or decision.route is None


def test_unanswerable_questions_are_refused_not_guessed(story) -> None:
    provider = FakeProvider(reply='{"route": "unanswerable", "confidence": 0.0}')
    decision = route_question(
        "what is my competitor's margin?", story.findings, provider
    )

    assert not decision.answerable
    assert "not something this data can answer" in decision.refusal.lower()
    assert decision.alternatives, "a refusal must still offer a way forward"


def test_malformed_model_output_falls_back_to_keywords(story) -> None:
    provider = FakeProvider(reply="I think probably the channel one?")
    decision = route_question("break it down by channel", story.findings, provider)

    assert decision.source == "keywords"
    assert decision.route.name == "break_down_by_channel"


def test_routes_needing_absent_columns_are_not_offered() -> None:
    """Never offer a chip the engine cannot answer."""
    result = analyse(
        fixtures.planted_business().drop(columns=["unit_cost", "customer_id"]),
        strict=True,
    )
    columns = set(result.frame.data.columns)
    names = {r.name for r in available_routes(result.findings, columns)}

    assert "most_profitable" not in names
    assert "which_customers" not in names


def test_chips_are_specific_vetted_questions(story) -> None:
    """Spec 6: guided follow-ups, not a blank filter panel."""
    columns = set(story.frame.data.columns)
    chips = suggest_chips(story.findings, columns)

    assert 0 < len(chips) <= 5
    assert all(isinstance(c, Route) and c.label.strip() for c in chips)
    assert all(not c.label.endswith(":") for c in chips)


def test_routing_answers_from_already_computed_findings(story) -> None:
    """Drill-down reads analysis that has already run; it recomputes nothing."""
    decision = route_question("why did it change?", story.findings, NullProvider())
    answer = answer_from_findings(decision, story.findings)

    assert answer is not None
    assert answer in story.findings


def test_empty_question_is_not_routed(story) -> None:
    decision = route_question("   ", story.findings, NullProvider())
    assert not decision.answerable


def test_every_route_has_an_intent_for_the_model() -> None:
    from busylab.narration.routing import ROUTES

    for route in ROUTES:
        assert route.intent and route.label
        assert route.name.islower()
