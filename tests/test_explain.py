"""Tests for the plain-language layer.

The point of this layer is that a reader with no business background can
understand what they are being told, so the tests are mostly about the prose
itself: that it exists for everything, that it stays factual, and that it does
not quietly become advice.
"""

from __future__ import annotations

import re

import pytest

from busylab.explain import (
    GLOSSARY,
    _BY_ID,
    _BY_TYPE,
    explain,
    glossary_for,
    terms_in,
)
from busylab.findings import (
    Evidence,
    Finding,
    FindingType,
    Severity,
    check_non_directive,
)

ALL_PROSE = {
    **{f"type:{k.value}": v for k, v in _BY_TYPE.items()},
    **{f"id:{k}": v for k, v in _BY_ID.items()},
    **{f"glossary:{k}": v for k, v in GLOSSARY.items()},
}


def _finding(**kwargs) -> Finding:
    base = dict(
        id="revenue_trend",
        type=FindingType.TREND,
        summary="Revenue is down 44% across the period.",
        facts={"change": -0.44},
        evidence=Evidence(method="ols", p_value=0.001),
        severity=Severity.URGENT,
    )
    base.update(kwargs)
    return Finding(**base)  # type: ignore[arg-type]


class TestNonDirective:
    """Spec 2 applies here more than anywhere, because the temptation is here.

    "What this means" sits one short step from "what you should do", and this
    module is where that step would get taken without anyone noticing.
    """

    @pytest.mark.parametrize("key", sorted(ALL_PROSE))
    def test_no_advice_anywhere(self, key):
        problems = check_non_directive(ALL_PROSE[key])
        assert not problems, f"{key}: {problems}"

    @pytest.mark.parametrize("key", sorted(ALL_PROSE))
    def test_no_second_person_instruction(self, key):
        # "you should", "you need to", "you must" are the phrasings that slip
        # past a keyword guard aimed at "consider" and "recommend".
        lowered = ALL_PROSE[key].lower()
        for phrase in ("you should", "you need to", "you must", "you can just"):
            assert phrase not in lowered, f"{key} contains {phrase!r}"


class TestNoNumbers:
    """The summary carries the numbers. Repeating them here adds risk, not sense."""

    @pytest.mark.parametrize("key", sorted(ALL_PROSE))
    def test_no_digits(self, key):
        text = ALL_PROSE[key]
        # 30p and 30% appear in the margin definition as an illustration of
        # what a margin is, which is a worked example rather than a claim
        # about this business's data.
        if key == "glossary:margin":
            return
        assert not re.search(r"\d", text), f"{key} contains a number"

    def test_the_margin_example_is_the_only_exception(self):
        with_digits = [k for k, v in ALL_PROSE.items() if re.search(r"\d", v)]
        assert with_digits == ["glossary:margin"]


class TestCoverage:
    def test_every_finding_type_is_explained(self):
        # A type with no explanation shows the reader a bare summary again,
        # which is the problem this module exists to fix.
        missing = [t.value for t in FindingType if t not in _BY_TYPE]
        assert not missing, f"unexplained finding types: {missing}"

    def test_every_explanation_is_more_than_a_restatement(self):
        # A one-clause explanation is almost always the summary again in
        # different words, which does not help. Glossary entries are exempt:
        # a definition earns its keep by being short, and "how many individual
        # items were sold" needs no padding.
        for key, text in ALL_PROSE.items():
            if key.startswith("glossary:"):
                continue
            assert len(text.split()) >= 8, f"{key} is too short to explain anything"

    def test_every_definition_says_something(self):
        for key, text in ALL_PROSE.items():
            if key.startswith("glossary:"):
                assert len(text.split()) >= 4, f"{key} defines nothing"

    def test_explanations_are_not_walls_of_text(self):
        for key, text in ALL_PROSE.items():
            assert len(text.split()) <= 70, f"{key} is too long to read in place"

    def test_prose_is_punctuated(self):
        for key, text in ALL_PROSE.items():
            assert text.strip().endswith("."), f"{key} does not end in a full stop"


class TestExplain:
    def test_a_specific_finding_beats_its_type(self):
        # The reader is stuck on this sentence, not on its category.
        finding = _finding(id="loss_making_product", type=FindingType.TENSION)
        assert explain(finding) == _BY_ID["loss_making_product"]
        assert explain(finding) != _BY_TYPE[FindingType.TENSION]

    def test_an_unknown_id_falls_back_to_the_type(self):
        finding = _finding(id="something_new", type=FindingType.CONCENTRATION)
        assert explain(finding) == _BY_TYPE[FindingType.CONCENTRATION]

    def test_every_type_returns_something(self):
        for kind in FindingType:
            assert explain(_finding(id="unmapped", type=kind))

    def test_the_explanation_is_not_the_summary(self):
        finding = _finding()
        assert explain(finding) != finding.summary


class TestGlossary:
    def test_terms_are_found_in_a_summary(self):
        finding = _finding(
            summary="Revenue is down 44%, larger than normal variation."
        )
        assert "revenue" in glossary_for(finding)
        assert "normal variation" in glossary_for(finding)

    def test_longer_terms_come_first(self):
        # So the UI offers "average order value" rather than matching "value"
        # inside it and defining the wrong thing.
        found = terms_in("average order value fell")
        assert found[0] == "average order value"

    def test_a_summary_with_no_jargon_needs_no_glossary(self):
        assert glossary_for(_finding(summary="Gift Box sold fewer than before.")) == {}

    def test_the_glossary_draws_on_the_summary_not_the_explanation(self):
        # The explanation is already written in words that need no gloss, so
        # glossing it would offer definitions for words the reader can read.
        finding = _finding(summary="Sales fell.", type=FindingType.CONCENTRATION)
        assert glossary_for(finding) == {}

    def test_significance_definition_separates_the_two_bars(self):
        # Conflating statistical and material significance is the exact
        # confusion the spec's two-bar rule exists to prevent, so the
        # definition has to hold them apart.
        text = GLOSSARY["significant"].lower()
        assert "does not mean" in text and "matter" in text


class TestSerialisation:
    def test_a_finding_carries_its_meaning_to_the_api(self):
        payload = _finding().to_dict()
        assert payload["meaning"]
        assert payload["meaning"] == explain(_finding())

    def test_the_glossary_travels_too(self):
        payload = _finding(summary="Revenue fell.").to_dict()
        assert "revenue" in payload["glossary"]

    def test_meaning_is_derived_not_stored(self):
        # Derived on the way out, so it cannot drift from the finding it
        # explains or go stale in a cached story.
        finding = _finding(id="loss_making_product")
        finding.id = "revenue_trend"
        assert finding.to_dict()["meaning"] == _BY_ID["revenue_trend"]
