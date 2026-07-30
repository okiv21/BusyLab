"""Turning structured findings into English.

The model's entire job is wording. Every number was computed by the analysis
engine and lives in ``finding.facts``; the model may restate those numbers and
nothing else (spec 2, 8).

That rule is enforced, not requested. Two checks run on every generated
sentence:

* **No invented numbers.** Every numeric token in the output must correspond to
  a value in ``facts``. A model that writes "revenue fell 23%" when the facts
  say 18% has computed something, which is precisely the failure the whole
  architecture exists to prevent.
* **No advice.** The same non-directive guard applied to the engine's own
  summaries (spec 2).

A sentence failing either check is discarded and the deterministic summary is
used instead. Falling back costs nothing: the engine's own sentence is already
correct, only plainer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..findings import Finding, check_non_directive
from .provider import Provider, ProviderError

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You write one sentence for a small business owner.

You are given a finding about their sales data as JSON. Every number has \
already been calculated. Your only job is wording.

Rules:
- Use ONLY numbers that appear in the JSON. Never calculate, estimate, \
combine or round into a new number.
- State what is true. Never tell the owner what to do, never suggest an \
action, never use "should", "consider", "try" or "recommend".
- Plain language, no jargon, no analytics vocabulary.
- One sentence, under 30 words.
- No preamble. Return the sentence only."""


@dataclass
class NarrationResult:
    """A sentence, and an honest account of where it came from."""

    text: str
    source: str  # "model" or "engine"
    rejected: list[str] = field(default_factory=list)

    @property
    def from_model(self) -> bool:
        return self.source == "model"


#: Matches numbers as a reader sees them: 18, 1,234.5, .5, 68%
_NUMBER = re.compile(r"\d[\d,]*\.?\d*")

#: Words that are numeric but carry no computed claim, so they are exempt.
_SAFE_WORDS = frozenset(
    {"one", "two", "three", "first", "second", "third", "half", "quarter"}
)


def _numeric_strings(value: object, out: set[str]) -> None:
    """Collect every way a fact value could legitimately be written."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        number = float(value)
        candidates = {
            f"{number:.0f}",
            f"{number:.1f}",
            f"{number:.2f}",
            f"{abs(number):.0f}",
            f"{abs(number):.1f}",
            f"{abs(number):.2f}",
        }
        # Percentages: facts store 0.18, prose says 18.
        for scaled in (number * 100, abs(number) * 100):
            candidates |= {f"{scaled:.0f}", f"{scaled:.1f}", f"{scaled:.2f}"}
        # Compact money: 551500 may be written 551.5k or 0.6m.
        for divisor in (1_000, 1_000_000):
            reduced = abs(number) / divisor
            if reduced >= 0.1:
                candidates |= {f"{reduced:.0f}", f"{reduced:.1f}", f"{reduced:.2f}"}
        out |= {c.rstrip("0").rstrip(".") if "." in c else c for c in candidates}
        out |= candidates
    elif isinstance(value, dict):
        for item in value.values():
            _numeric_strings(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _numeric_strings(item, out)
    elif isinstance(value, str):
        for match in _NUMBER.finditer(value):
            out.add(match.group().replace(",", ""))


def allowed_numbers(finding: Finding) -> set[str]:
    """Every numeric string the model is permitted to write."""
    out: set[str] = set()
    _numeric_strings(finding.facts, out)
    # Evidence values are quotable too, e.g. a sample size.
    _numeric_strings(
        {
            "n": finding.evidence.sample_size,
            "p": finding.evidence.p_value,
            "adj": finding.evidence.adjusted_p,
        },
        out,
    )
    return {s for s in out if s}


def invented_numbers(text: str, finding: Finding) -> list[str]:
    """Numbers in ``text`` that do not come from the finding's facts."""
    permitted = allowed_numbers(finding)
    found: list[str] = []
    for match in _NUMBER.finditer(text):
        raw = match.group()
        normalised = raw.replace(",", "")
        trimmed = (
            normalised.rstrip("0").rstrip(".") if "." in normalised else normalised
        )
        if normalised in permitted or trimmed in permitted:
            continue
        found.append(raw)
    return found


def verify(text: str, finding: Finding) -> list[str]:
    """Reasons ``text`` may not be used. Empty means it is safe."""
    problems: list[str] = []
    invented = invented_numbers(text, finding)
    if invented:
        problems.append(f"invented numbers not in the data: {', '.join(invented)}")
    problems.extend(check_non_directive(text))
    if len(text.split()) > 45:
        problems.append("too long to sit under a chart")
    if not text.strip():
        problems.append("empty")
    return problems


def _build_prompt(finding: Finding) -> str:
    payload = {
        "finding_type": finding.type.value,
        "severity": finding.severity.value,
        "certainty": finding.evidence.strength,
        "facts": finding.facts,
        "engine_sentence": finding.summary,
    }
    import json

    return (
        "Rewrite the engine_sentence more naturally, using only the numbers in "
        "facts.\n\n" + json.dumps(payload, default=str, indent=2)
    )


def narrate(
    finding: Finding,
    provider: Provider,
    *,
    cache: dict[str, str] | None = None,
) -> NarrationResult:
    """Produce the sentence shown under a finding's chart.

    Falls back to the engine's own wording whenever the model is unavailable
    or its output fails verification, so this function always returns something
    correct.
    """
    if not provider.available():
        return NarrationResult(finding.summary, "engine")

    key = cache_key(finding)
    if cache is not None and key in cache:
        return NarrationResult(cache[key], "model")

    try:
        raw = provider.complete(
            SYSTEM_PROMPT, _build_prompt(finding), max_tokens=120, temperature=0.3
        )
    except ProviderError as exc:
        log.info("narration unavailable, using engine wording: %s", exc)
        return NarrationResult(finding.summary, "engine")

    text = raw.strip().strip('"').split("\n")[0].strip()
    problems = verify(text, finding)
    if problems:
        log.warning("rejected narration for %s: %s", finding.id, problems[0])
        return NarrationResult(finding.summary, "engine", rejected=problems)

    if cache is not None:
        cache[key] = text
    return NarrationResult(text, "model")


def cache_key(finding: Finding) -> str:
    """Stable key for a finding's numbers.

    Narration is cached per finding because the sentence does not change until
    the numbers do (spec 9). Keying on the facts rather than on the id means a
    refresh that moves the numbers correctly invalidates the cache.
    """
    import hashlib
    import json

    blob = json.dumps(
        {"id": finding.id, "facts": finding.facts}, sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def narrate_all(
    findings: list[Finding],
    provider: Provider,
    *,
    cache: dict[str, str] | None = None,
) -> list[NarrationResult]:
    """Narrate a whole story, one small call each.

    One finding per call rather than one batched call: the free tier's binding
    limit is tokens per minute, and small cached payloads sit inside it far
    more comfortably than a single large one.
    """
    return [narrate(f, provider, cache=cache) for f in findings]
