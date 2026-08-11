"""Tests for free-text answering and, mostly, for its rejections.

This module lets the model say something the engine did not write, so what
matters is not that a good answer passes but that each specific way an answer
can be wrong is actually caught. Every check has a test that a wrong answer is
rejected *and* a test that a correct answer is not, because a guard that
rejects everything is as useless as no guard.
"""

from __future__ import annotations

import json

import pytest

from busylab.findings import Evidence, Finding, FindingType, Severity
from busylab.narration.answer import (
    AI_CAUTION,
    Answer,
    answer_question,
    entity_names,
    suggest,
    verify_answer,
)


def _trend() -> Finding:
    return Finding(
        id="revenue_trend",
        type=FindingType.TREND,
        summary="Revenue is down 44% across the period.",
        facts={"direction": "down", "change_pct": -0.44, "periods": 24},
        evidence=Evidence(method="ols", p_value=0.001, sample_size=24),
        severity=Severity.URGENT,
    )


def _decomposition() -> Finding:
    return Finding(
        id="revenue_decomposition",
        type=FindingType.DECOMPOSITION,
        summary="Linen Candle accounts for 38% of the fall.",
        facts={
            "biggest": "Linen Candle",
            "share": 0.38,
            "total_change": -551500.0,
        },
        evidence=Evidence(method="contribution", p_value=0.004, sample_size=24),
        severity=Severity.WATCH,
    )


def _weak() -> Finding:
    return Finding(
        id="segmentation_channel",
        type=FindingType.SEGMENTATION,
        summary="Channels differ a little.",
        facts={"gap_pct": 0.04},
        evidence=Evidence(method="anova", p_value=0.42, sample_size=20),
        severity=Severity.NEUTRAL,
    )


ALL = [_trend(), _decomposition(), _weak()]


class TestCitationIsRequired:
    def test_no_citation_is_rejected(self):
        # Without a citation there is nothing to check against, so every other
        # check would pass vacuously.
        problems = verify_answer("Revenue is down 44%.", ALL, [])
        assert problems == ["no finding was cited, so nothing supports this answer"]

    def test_an_unknown_citation_alone_is_rejected(self):
        problems = verify_answer("Revenue is down 44%.", ALL, ["invented_id"])
        assert "no finding was cited" in problems[0]

    def test_a_real_citation_passes(self):
        assert verify_answer("Revenue is down 44%.", ALL, ["revenue_trend"]) == []


class TestNumberGuard:
    def test_a_number_from_the_cited_finding_is_allowed(self):
        assert verify_answer("Revenue fell 44%.", ALL, ["revenue_trend"]) == []

    def test_an_invented_number_is_caught(self):
        problems = verify_answer("Revenue fell 51%.", ALL, ["revenue_trend"])
        assert any("51" in p for p in problems)

    def test_a_number_from_an_uncited_finding_is_caught(self):
        # This is the splice: 38% is real, but it belongs to a finding the
        # answer did not cite, and pairing it with the trend implies a link
        # nothing measured.
        problems = verify_answer("Revenue fell 44%, and 38% of that is one line.", ALL, ["revenue_trend"])
        assert any("38" in p for p in problems)

    def test_citing_both_findings_permits_both_numbers(self):
        problems = verify_answer(
            "Revenue fell 44%. Linen Candle accounts for 38% of that.",
            ALL,
            ["revenue_trend", "revenue_decomposition"],
        )
        assert problems == []


class TestCausalGuard:
    def test_cause_without_a_decomposition_is_rejected(self):
        problems = verify_answer(
            "Revenue fell 44% because fewer people bought.", ALL, ["revenue_trend"]
        )
        assert any("claims cause" in p for p in problems)

    def test_cause_with_a_decomposition_is_allowed(self):
        problems = verify_answer(
            "Revenue fell because Linen Candle accounts for 38% of the move.",
            ALL,
            ["revenue_trend", "revenue_decomposition"],
        )
        assert problems == []

    @pytest.mark.parametrize(
        "phrase",
        ["because", "due to", "driven by", "as a result of", "led to", "thanks to"],
    )
    def test_every_causal_phrasing_is_covered(self, phrase):
        problems = verify_answer(
            f"Revenue fell {phrase} something.", ALL, ["revenue_trend"]
        )
        assert any("claims cause" in p for p in problems), phrase

    def test_a_non_causal_answer_is_not_flagged(self):
        problems = verify_answer(
            "Revenue fell 44% over the period.", ALL, ["revenue_trend"]
        )
        assert not any("claims cause" in p for p in problems)


class TestDirectionGuard:
    def test_claiming_a_rise_over_a_fall_is_caught(self):
        problems = verify_answer("Revenue rose 44%.", ALL, ["revenue_trend"])
        assert any("rose" in p for p in problems)

    def test_claiming_a_fall_over_a_fall_is_fine(self):
        assert verify_answer("Revenue fell 44%.", ALL, ["revenue_trend"]) == []

    def test_a_mixed_answer_is_not_rejected(self):
        # An answer about both a rise and a fall is usually the interesting
        # kind, and a stricter check would reject it.
        mixed = Finding(
            id="repeat_vs_new",
            type=FindingType.REPEAT_VS_NEW,
            summary="Returning up, new down.",
            facts={"repeat_change": 0.11, "new_change": -0.98},
            evidence=Evidence(method="split", p_value=0.01),
        )
        problems = verify_answer(
            "Returning customers rose while first-time buyers fell.",
            [mixed],
            ["repeat_vs_new"],
        )
        assert problems == []

    def test_no_direction_claim_means_no_check(self):
        assert verify_answer("Revenue is 44% different.", ALL, ["revenue_trend"]) == []


class TestEntityGuard:
    def test_a_real_product_is_allowed(self):
        problems = verify_answer(
            "Linen Candle accounts for 38% of the fall.",
            ALL,
            ["revenue_decomposition"],
        )
        assert problems == []

    def test_an_invented_product_is_caught(self):
        # As wrong as an invented number, and far more plausible-looking.
        problems = verify_answer(
            "Bamboo Diffuser accounts for 38% of the fall.",
            ALL,
            ["revenue_decomposition"],
        )
        assert any("names not in the data" in p for p in problems)

    def test_entity_names_are_collected_from_facts(self):
        assert "linen candle" in entity_names(ALL)

    def test_entity_names_ignore_long_sentences(self):
        # facts can hold prose; a sentence is not a label.
        wordy = Finding(
            id="x",
            type=FindingType.TREND,
            summary="s",
            facts={"note": "a" * 80},
        )
        assert "a" * 80 not in entity_names([wordy])


class TestCertaintyGuard:
    def test_a_weak_finding_stated_flatly_is_rejected(self):
        problems = verify_answer(
            "Channels differ by 4%.", ALL, ["segmentation_channel"]
        )
        assert any("uncertain" in p for p in problems)

    def test_a_weak_finding_hedged_is_allowed(self):
        problems = verify_answer(
            "Channels appear to differ by 4%, though not clearly.",
            ALL,
            ["segmentation_channel"],
        )
        assert problems == []

    def test_a_strong_finding_needs_no_hedge(self):
        assert verify_answer("Revenue fell 44%.", ALL, ["revenue_trend"]) == []

    def test_one_strong_citation_carries_a_weak_one(self):
        # Only every cited finding being weak forces a hedge; otherwise an
        # answer resting mainly on solid evidence would be made to sound
        # less certain than it is.
        problems = verify_answer(
            "Revenue fell 44% and channels differ by 4%.",
            ALL,
            ["revenue_trend", "segmentation_channel"],
        )
        assert not any("uncertain" in p for p in problems)


class TestNonDirective:
    def test_advice_is_rejected(self):
        problems = verify_answer(
            "Revenue fell 44%, so you should drop that line.",
            ALL,
            ["revenue_trend"],
        )
        assert problems

    def test_length_is_capped(self):
        # The cap is generous on purpose: "explain this so I can understand it"
        # deserves a paragraph, and a tight cap rejected the honest answer and
        # fell back to a one-line finding that ignored the question.
        problems = verify_answer(
            "Revenue fell 44%. " + "word " * 200, ALL, ["revenue_trend"]
        )
        assert any("too long" in p for p in problems)

    def test_a_paragraph_length_explanation_is_allowed(self):
        assert verify_answer(
            "Revenue fell 44%. " + "word " * 100, ALL, ["revenue_trend"]
        ) == []

    def test_empty_is_rejected(self):
        assert verify_answer("   ", ALL, ["revenue_trend"]) == ["empty"]


class _Provider:
    """A stand-in model returning whatever the test needs."""

    name = "stub"

    def __init__(self, reply: str, available: bool = True):
        self.reply = reply
        self._available = available
        self.calls: list[tuple[str, str]] = []

    def available(self) -> bool:
        return self._available

    def complete(self, system, user, *, max_tokens=200, temperature=0.2):
        self.calls.append((system, user))
        return self.reply


class TestAnswerQuestion:
    def test_a_verified_answer_is_returned(self):
        provider = _Provider(
            json.dumps({"answer": "Revenue fell 44%.", "used": ["revenue_trend"]})
        )
        result = answer_question("how is revenue?", ALL, provider)
        assert result.generated
        assert result.text == "Revenue fell 44%."
        assert result.sources == ["revenue_trend"]

    def test_a_rejected_answer_falls_back_to_the_engine(self):
        # The floor of this feature is the ceiling of the routing it replaces.
        provider = _Provider(
            json.dumps({"answer": "Revenue fell 99%.", "used": ["revenue_trend"]})
        )
        result = answer_question(
            "how is revenue?", ALL, provider, fallback=_trend()
        )
        assert not result.generated
        # Labelled, not passed off as an answer to the question asked.
        assert "could not answer that directly" in result.text
        assert _trend().summary in result.text
        assert result.rejected

    def test_with_no_model_it_behaves_as_before(self):
        provider = _Provider("", available=False)
        result = answer_question("q", ALL, provider, fallback=_trend())
        assert _trend().summary in result.text
        assert result.origin == "engine"
        # No model means no suggestions either.
        assert not result.has_advice

    def test_with_no_model_and_no_fallback_it_refuses(self):
        provider = _Provider("", available=False)
        result = answer_question("q", ALL, provider)
        # Saying so is the correct outcome; inventing an answer is not.
        assert result.text == "That is not something this data can answer."
        assert not result.generated

    def test_unparseable_output_falls_back(self):
        provider = _Provider("I think revenue went down a lot")
        result = answer_question("q", ALL, provider, fallback=_trend())
        assert _trend().summary in result.text
        assert "JSON" in result.rejected[0]

    def test_a_typo_citation_alongside_a_real_one_still_verifies(self):
        provider = _Provider(
            json.dumps(
                {"answer": "Revenue fell 44%.", "used": ["revenue_trend", "typo_id"]}
            )
        )
        result = answer_question("q", ALL, provider)
        assert result.generated
        assert result.sources == ["revenue_trend"]

    def test_the_findings_are_actually_sent_to_the_model(self):
        provider = _Provider(
            json.dumps({"answer": "Revenue fell 44%.", "used": ["revenue_trend"]})
        )
        answer_question("how is revenue?", ALL, provider)
        _, user = provider.calls[0]
        assert "revenue_trend" in user and "how is revenue?" in user

    def test_the_prompt_forbids_calculating(self):
        provider = _Provider(json.dumps({"answer": "x", "used": []}))
        answer_question("q", ALL, provider)
        system, _ = provider.calls[0]
        assert "Never calculate" in system
        assert "decomposition" in system

    def test_the_answer_call_is_deterministic(self):
        # Answering is retrieval and wording, not invention. Advice is a
        # separate call and is allowed to be warmer, so this checks the first.
        seen = []

        class _Recorder(_Provider):
            def complete(self, system, user, *, max_tokens=200, temperature=0.2):
                seen.append(temperature)
                return self.reply

        provider = _Recorder(
            json.dumps({"answer": "Revenue fell 44%.", "used": ["revenue_trend"]})
        )
        answer_question("q", ALL, provider, with_advice=False)
        assert seen[0] == 0.0

    def test_an_empty_question_does_not_reach_the_model(self):
        provider = _Provider("should not be called")
        answer_question("  ", ALL, provider, fallback=_trend())
        assert provider.calls == []


class TestAdvice:
    """Suggestions are a deliberate exception to the non-directive rule.

    Being told only what is true and never what it might mean turned out to be
    unhelpful to a reader without a business background, so this section is
    allowed to suggest actions. What must not slip is the honesty around it:
    the numbers are still checked, and the caution is not optional.
    """

    def test_advice_may_be_directive(self):
        # The whole point. check_non_directive must not be applied here.
        provider = _Provider("Consider dropping the line that loses money.")
        text, caution = suggest("what should I do?", ALL, provider)
        assert "Consider" in text
        assert caution == AI_CAUTION

    def test_the_caution_always_accompanies_advice(self):
        provider = _Provider("Try focusing on the returning customers.")
        text, caution = suggest("q", ALL, provider)
        assert text and caution
        # Never one without the other.
        assert bool(text) == bool(caution)

    def test_invented_numbers_are_still_rejected(self):
        # A wrong figure is wrong whichever section it appears in.
        provider = _Provider("Revenue fell 87%, so consider cutting stock.")
        text, caution = suggest("q", ALL, provider)
        assert text == "" and caution == ""

    def test_real_numbers_are_allowed(self):
        provider = _Provider("Revenue fell 44%, so the trend is worth watching.")
        text, _ = suggest("q", ALL, provider)
        assert "44" in text

    def test_no_model_means_no_advice(self):
        provider = _Provider("...", available=False)
        assert suggest("q", ALL, provider) == ("", "")

    def test_no_findings_means_no_advice(self):
        provider = _Provider("something")
        assert suggest("q", [], provider) == ("", "")

    def test_an_empty_reply_produces_nothing(self):
        assert suggest("q", ALL, _Provider("   ")) == ("", "")

    def test_a_rambling_reply_is_dropped(self):
        # No suggestion beats a wall of text nobody reads.
        assert suggest("q", ALL, _Provider("word " * 300)) == ("", "")

    def test_the_prompt_permits_action_and_forbids_invention(self):
        provider = _Provider("fine")
        suggest("q", ALL, provider)
        system, user = provider.calls[0]
        assert "allowed to suggest actions" in system
        assert "Do not invent numbers" in system
        assert "revenue_trend" in user

    def test_the_question_reaches_the_model(self):
        provider = _Provider("fine")
        suggest("why is revenue down?", ALL, provider)
        assert "why is revenue down?" in provider.calls[0][1]

    def test_advice_is_attached_to_a_verified_answer(self):
        provider = _Provider(
            json.dumps({"answer": "Revenue fell 44%.", "used": ["revenue_trend"]})
        )
        result = answer_question("q", ALL, provider)
        # The stub returns the same reply to both calls, so the advice call
        # gets JSON back - which contains a real number and passes.
        assert result.generated
        assert result.caution == AI_CAUTION if result.advice else True

    def test_advice_survives_a_rejected_answer(self):
        # The findings are computed either way, so withholding suggestions
        # because the wording failed verification would help nobody.
        provider = _Provider("Consider watching the falling line.")
        result = answer_question("q", ALL, provider, fallback=_trend())
        assert not result.generated
        assert result.has_advice
        assert result.caution == AI_CAUTION

    def test_advice_can_be_turned_off(self):
        provider = _Provider(
            json.dumps({"answer": "Revenue fell 44%.", "used": ["revenue_trend"]})
        )
        result = answer_question("q", ALL, provider, with_advice=False)
        assert not result.has_advice
