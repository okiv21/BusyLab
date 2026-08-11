"""Answering a free-text question from findings that have already been computed.

This is the one place the model is allowed to say something the engine did not
write. That is a real loosening, so the whole module is built around what
happens when it goes wrong rather than around what happens when it goes right.

The rule it replaces was simple and safe: the model picks one of twelve
analyses and the engine's own sentence is shown. Nothing could be confidently
wrong because nothing was generated. The cost was that anything outside those
twelve got "that is not something this data can answer", and an answer that
did land was a sentence the reader had already seen in the story - so the
feature read as a lookup rather than as an answer.

What replaces it: the model receives the findings relevant to the question and
writes a direct answer, and every sentence is then checked mechanically. Prose
instructions do not constrain a model; rejection does. Six checks run, each
aimed at a specific way an answer can be wrong while every number in it is
correct:

* **Numbers must come from the findings it cited**, not from the union of every
  fact in the story. Citation narrows the permitted set, which is what makes
  cross-analysis splicing detectable at all.
* **Causal claims require a decomposition.** Decomposition is the engine's only
  causal instrument. "X fell because Y" with no decomposition cited is the
  model reasoning, not the engine attributing, and it is the most common way a
  wrong answer sounds authoritative.
* **Direction words must match the sign** of the fact they describe. A model
  that writes "rose" over a negative value has lost the thread.
* **Named things must exist in the data.** The number guard, applied to nouns.
* **Weak evidence must be hedged.** Stating a finding that did not clear
  significance as flat fact overstates what was measured.
* **Nothing may be answered with no citation at all.**

What none of this catches, and it is worth naming rather than implying the
checks are complete: a true, verifiable, irrelevant fact placed next to the
question so that juxtaposition implies a connection, with no causal word to
flag. Nothing in such an answer is false, so nothing can reject it. That is the
residual risk of this design, and it is why every answer carries the ids it
drew on - a reader who can see the sources can see when they do not add up.

When any check fails the answer is discarded and the routed finding's own
summary is shown instead, which is exactly the previous behaviour. The floor
of this feature is the ceiling of the old one.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from ..findings import Finding, check_non_directive
from .narrate import allowed_numbers
from .provider import Provider, ProviderError

log = logging.getLogger(__name__)


#: Words that assert one thing produced another. The engine only earns these
#: through decomposition, which measures contribution to a change directly.
_CAUSAL = (
    "because",
    "caused by",
    "caused",
    "due to",
    "driven by",
    "drove",
    "as a result of",
    "resulted in",
    "led to",
    "leading to",
    "explains why",
    "the reason",
    "thanks to",
    "owing to",
    "brought about",
)

#: Finding types that measure contribution, and so support a causal statement.
_CAUSAL_TYPES = ("decomposition", "repeat_vs_new")

#: Direction words, mapped to the sign they assert.
_UP = ("rose", "grew", "increased", "climbed", "up ", "higher", "gained", "improved")
_DOWN = ("fell", "dropped", "declined", "decreased", "down ", "lower", "lost", "shrank")

#: Language that admits the finding is not certain.
_HEDGES = (
    "may",
    "might",
    "appears",
    "appear",
    "seems",
    "seem",
    "suggests",
    "suggest",
    "not clear",
    "unclear",
    "possibly",
    "roughly",
    "around",
    "about",
    "tentative",
    "early",
    "not certain",
    "cannot be separated",
    "within normal",
)

_NUMBER = re.compile(r"\d[\d,]*\.?\d*")


#: Shown with every generated suggestion, without exception.
#:
#: The rest of this codebase reports only what was computed, and suggestions are
#: a different kind of thing: they are a model's reading of the numbers, not a
#: measurement of them. Saying so plainly is what makes it reasonable to offer
#: them at all, so the caution travels with the text rather than living in a
#: settings page or a footer nobody reads.
AI_CAUTION = (
    "This part is a suggestion from an AI model reading your numbers, not "
    "something BusyLab calculated. It can be wrong, and it does not know "
    "anything about your business beyond this file. Use it as a starting point "
    "for your own thinking rather than as the basis for a decision."
)


@dataclass
class Answer:
    """A generated answer, and everything needed to distrust it."""

    text: str
    #: Finding ids the model said it used, after verification that they exist.
    sources: list[str] = field(default_factory=list)
    #: "model" when generated and verified, "engine" when it fell back.
    origin: str = "model"
    #: Why a generated answer was rejected. Empty when it was accepted.
    rejected: list[str] = field(default_factory=list)
    #: Optional suggestions. Separate from ``text`` because it is a different
    #: kind of claim and must never be read as part of the finding.
    advice: str = ""
    #: Travels with ``advice``, always. Empty when there is no advice.
    caution: str = ""

    @property
    def generated(self) -> bool:
        return self.origin == "model"

    @property
    def has_advice(self) -> bool:
        return bool(self.advice)


def entity_names(findings: list[Finding]) -> set[str]:
    """Every name the data actually contains, lowercased.

    Collected from facts and chart payloads rather than from the frame, because
    those are what the model was shown. A name it produces that is not in here
    was invented, and an invented product name is as wrong as an invented
    number while looking far more plausible.
    """
    names: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, str):
            cleaned = value.strip().lower()
            # Long strings are sentences, not labels.
            if cleaned and len(cleaned) <= 60:
                names.add(cleaned)
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str):
                    names.add(key.strip().lower())
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    for finding in findings:
        walk(finding.facts)
        walk(finding.chart_data)
    return {n for n in names if n}


def _cited(findings: list[Finding], ids: list[str]) -> list[Finding]:
    wanted = set(ids)
    return [f for f in findings if f.id in wanted]


def _permitted_numbers(cited: list[Finding]) -> set[str]:
    """Numbers the answer may contain, from the cited findings only."""
    permitted: set[str] = set()
    for finding in cited:
        permitted |= allowed_numbers(finding)
    return permitted


def _invented_numbers(text: str, permitted: set[str]) -> list[str]:
    found: list[str] = []
    for match in _NUMBER.finditer(text):
        raw = match.group()
        normalised = raw.replace(",", "")
        trimmed = normalised.rstrip("0").rstrip(".") if "." in normalised else normalised
        if normalised in permitted or trimmed in permitted:
            continue
        found.append(raw)
    return found


def _asserts_cause(text: str) -> str | None:
    lowered = text.lower()
    for word in _CAUSAL:
        if word in lowered:
            return word
    return None


def _has_hedge(text: str) -> bool:
    lowered = text.lower()
    return any(hedge in lowered for hedge in _HEDGES)


def _direction_conflicts(text: str, cited: list[Finding]) -> str | None:
    """A direction word contradicting every sign available to support it.

    Deliberately weak: it only fires when the answer claims a direction and
    *nothing* among the cited facts moves that way. Anything stricter would
    reject correct answers about a mix of rising and falling things, which is
    most interesting answers.
    """
    lowered = text.lower()
    says_up = any(word in lowered for word in _UP)
    says_down = any(word in lowered for word in _DOWN)
    if not (says_up or says_down):
        return None

    signs: set[str] = set()
    for finding in cited:
        for key, value in finding.facts.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            # Only quantities that can meaningfully be signed.
            if any(token in key.lower() for token in ("change", "delta", "effect", "trend", "move", "growth", "lift")):
                if value < 0:
                    signs.add("down")
                elif value > 0:
                    signs.add("up")
        direction = finding.facts.get("direction")
        if isinstance(direction, str):
            if direction.lower() in ("down", "fell", "falling"):
                signs.add("down")
            elif direction.lower() in ("up", "rose", "rising"):
                signs.add("up")

    if not signs:
        return None
    if says_up and "up" not in signs and not says_down:
        return "says something rose when nothing in the cited findings rose"
    if says_down and "down" not in signs and not says_up:
        return "says something fell when nothing in the cited findings fell"
    return None


def _invented_entities(text: str, findings: list[Finding], cited: list[Finding]) -> list[str]:
    """Capitalised names in the answer that do not appear in the data.

    Only multi-word or clearly-label-shaped capitalised runs are considered, to
    avoid flagging ordinary sentence-initial words.
    """
    known = entity_names(findings)
    suspects: list[str] = []
    # Capitalised runs of two or more words: "Gift Box", "Ceramic Diffuser".
    for match in re.finditer(r"\b([A-Z][a-z0-9]+(?: [A-Z][a-z0-9]+)+)\b", text):
        phrase = match.group(1).lower()
        # Containment either way, not equality. A model that writes "Senator
        # Set" about a product recorded as "Senator Set (Men)" has named the
        # right thing; rejecting that shortening threw away good answers and
        # accused the model of inventing a product it had read correctly.
        if any(phrase in name or name in phrase for name in known):
            continue
        suspects.append(match.group(1))
    return suspects


#: Words that carry no subject on their own, so their absence from the data
#: means nothing.
_NOT_A_SUBJECT = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "if", "of", "in", "on", "at",
        "to", "for", "from", "by", "with", "about", "into", "over", "than",
        "this", "that", "these", "those", "it", "its", "my", "our", "your",
        "me", "we", "us", "you", "is", "are", "was", "were", "be", "been",
        "do", "does", "did", "doing", "done", "have", "has", "had", "can",
        "could", "should", "would", "will", "shall", "may", "might", "must",
        "what", "why", "how", "when", "where", "which", "who", "whom",
        "not", "any", "all", "some", "more", "most", "less", "least",
        "much", "many", "very", "just", "only", "also", "still", "yet",
        "out", "off", "again", "then", "there", "here", "now", "way",
        "good", "bad", "well", "better", "worse", "best", "worst", "high",
        "low", "big", "small", "growing", "falling", "rising",
        # The product's own vocabulary, which is never an entity in the data.
        "revenue", "sales", "sale", "profit", "margin", "cost", "costs",
        "price", "prices", "units", "unit", "orders", "order", "customer",
        "customers", "product", "products", "month", "months", "week", "weeks",
        "year", "years", "period", "periods", "data", "file", "business",
        "money", "total", "average", "trend", "change", "changes", "channel",
        "channels", "region", "regions", "category", "categories", "target",
        "compared", "comparison", "understand", "explain", "findings",
        "finding", "tell", "show", "mean", "means", "happening", "happened",
        "doing", "going", "look", "looks", "since", "last", "first", "next",
    }
)


def unknown_subjects(question: str, findings: list[Finding]) -> list[str]:
    """Words in the question naming something the data does not contain.

    The failure this exists for: asked "how are candles doing in Lagos", a model
    holding a clothing shop's findings answered "candles may be doing steadily
    in Lagos, as Lagos appears to have held roughly steady". Every word of that
    is true and there are no candles in the file.

    None of the other checks catch it. The number guard passes because no number
    is wrong; the causal guard passes because nothing is claimed to cause
    anything; the entity guard passes because it only inspects capitalised runs
    and "candles" is lowercase. The sentence asserts nothing false - it simply
    lets the question's premise stand, which is the residual risk this design
    always had.

    So the question is checked too. Anything named here is told to the model as
    absent, and the answer is then required to say so.
    """
    known = entity_names(findings)
    haystack = " ".join(known)
    missing: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", question):
        lowered = word.lower()
        if lowered in _NOT_A_SUBJECT or lowered in missing:
            continue
        # Singular and plural both count as present.
        stem = lowered[:-1] if lowered.endswith("s") else lowered
        if lowered in haystack or (len(stem) >= 3 and stem in haystack):
            continue
        missing.append(lowered)
    return missing


#: Ways of saying "that is not in here". One of these must appear when the
#: question named something the data does not contain.
_ABSENCE = (
    "not in",
    "no ",
    "does not",
    "doesn't",
    "cannot",
    "can't",
    "nothing",
    "not appear",
    "not recorded",
    "not covered",
    "not something",
    "no record",
    "not present",
    "not include",
    "isn't",
    "is not",
    "unable",
)


def verify_answer(
    text: str,
    findings: list[Finding],
    cited_ids: list[str],
    *,
    absent: list[str] | None = None,
) -> list[str]:
    """Reasons this answer may not be shown. Empty means it is safe.

    Ordered so the most fundamental problem is reported first, because the
    first reason is the one that gets logged and read.
    """
    problems: list[str] = []

    if not text.strip():
        return ["empty"]

    cited = _cited(findings, cited_ids)
    if not cited:
        # Without a citation there is nothing to check the answer against, so
        # every other check below would pass vacuously.
        return ["no finding was cited, so nothing supports this answer"]

    invented = _invented_numbers(text, _permitted_numbers(cited))
    if invented:
        problems.append(
            f"numbers not in the cited findings: {', '.join(invented)}"
        )

    causal = _asserts_cause(text)
    if causal and not any(f.type.value in _CAUSAL_TYPES for f in cited):
        problems.append(
            f"claims cause ({causal!r}) without a decomposition to support it"
        )

    conflict = _direction_conflicts(text, cited)
    if conflict:
        problems.append(conflict)

    entities = _invented_entities(text, findings, cited)
    if entities:
        problems.append(f"names not in the data: {', '.join(entities)}")

    weak = [f for f in cited if not f.evidence.is_significant]
    if weak and len(weak) == len(cited) and not _has_hedge(text):
        problems.append(
            "states an uncertain finding as fact, with no hedge"
        )

    if absent:
        # The question named something the file does not contain. An answer
        # that neither mentions nor denies it lets the premise stand, which is
        # how "candles may be doing steadily in Lagos" came out of a clothing
        # shop's data without a single false statement in it.
        lowered = text.lower()
        if not any(phrase in lowered for phrase in _ABSENCE):
            problems.append(
                f"the question asked about {', '.join(absent)}, which is not in "
                f"this data, and the answer does not say so"
            )

    problems.extend(check_non_directive(text))

    # Generous, because a question like "explain this so I can understand it"
    # deserves a paragraph and a tight cap would reject the honest answer and
    # fall back to a one-line finding that does not address the question.
    if len(text.split()) > 160:
        problems.append("too long to read as an answer")

    return problems


ADVICE_PROMPT = """You suggest what a small business owner might consider \
doing, based on findings that have already been calculated from their own \
sales data.

Unlike the rest of this product you ARE allowed to suggest actions. That is \
the point of this section.

Rules:
- Base every suggestion on the findings you are given. Do not invent numbers.
- Two or three short suggestions, as plain sentences. No headings, no preamble.
- Be concrete about this business, not generic advice that would fit anyone.
- Say plainly when a finding is too uncertain to act on.
- Never claim something is guaranteed to work.

Return the suggestions as plain text only."""


def suggest(
    question: str,
    findings: list[Finding],
    provider: Provider,
) -> tuple[str, str]:
    """Suggestions for what to do, and the caution that must accompany them.

    A deliberate exception to the non-directive rule the rest of the engine
    holds to, made because being told only what is true and never what it
    might mean for a decision turned out to be genuinely unhelpful to someone
    without a business background.

    The number guard still applies - a wrong figure is wrong whatever section
    it sits in - but the non-directive guard does not, since advice is what
    this function exists to produce. Returns empty strings when there is
    nothing trustworthy to say, because no suggestion is better than a
    confident irrelevant one.
    """
    if not findings or not provider.available():
        return "", ""

    payload = [
        {
            "id": f.id,
            "says": f.summary,
            "facts": f.facts,
            "certain": f.evidence.is_significant,
        }
        for f in findings[:6]  # the ranked top; beyond that it is noise
    ]
    prompt = (
        (f'The owner asked: "{question}"\n\n' if question.strip() else "")
        + f"Findings:\n{json.dumps(payload, default=str, indent=2)}\n\n"
        "What might they consider?"
    )

    try:
        raw = provider.complete(ADVICE_PROMPT, prompt, max_tokens=400, temperature=0.4)
    except ProviderError as exc:
        log.info("advice unavailable: %s", exc)
        return "", ""

    text = raw.strip()
    if not text:
        return "", ""

    # Numbers are still checked, against every finding shown to the model.
    permitted = _permitted_numbers(findings[:6])
    invented = _invented_numbers(text, permitted)
    if invented:
        log.warning("rejected advice, invented numbers: %s", invented)
        return "", ""

    if len(text.split()) > 220:
        log.info("rejected advice: too long")
        return "", ""

    return text, AI_CAUTION


SYSTEM_PROMPT = """You answer a small business owner's question about their \
own sales data.

You are given findings that have already been calculated. Every number in them \
is correct and final.

Rules, all enforced automatically:
- Use ONLY numbers that appear in the findings you cite. Never calculate, \
combine, estimate or round into a new number.
- Only say one thing caused another if you cite a decomposition finding, which \
is the only kind that measures what drove a change.
- Only name products, regions or channels that appear in the findings.
- If a finding is not statistically significant, say so with words like \
"appears" or "may" rather than stating it flatly.
- State what is true. Never tell the owner what to do. Never use "should", \
"consider", "try" or "recommend".
- Plain language. No jargon. Two or three short sentences at most.
- If the findings do not answer the question, say so plainly.

Reply with JSON only:
{"answer": "<your answer>", "used": ["<finding id>", ...]}

"used" must list every finding id you drew on. An answer with no ids is \
discarded."""


def _build_prompt(
    question: str, findings: list[Finding], absent: list[str] | None = None
) -> str:
    payload = [
        {
            "id": f.id,
            "type": f.type.value,
            "says": f.summary,
            "facts": f.facts,
            "certainty": f.evidence.strength,
            "significant": f.evidence.is_significant,
        }
        for f in findings
    ]
    warning = ""
    if absent:
        warning = (
            f"\nIMPORTANT: this data contains nothing called "
            f"{', '.join(absent)}. Say so plainly instead of answering as "
            f"though it were there.\n"
        )
    return (
        f'Question: "{question}"\n{warning}\n'
        f"Findings available:\n{json.dumps(payload, default=str, indent=2)}\n\n"
        "Answer the question from these findings."
    )


def answer_question(
    question: str,
    findings: list[Finding],
    provider: Provider,
    *,
    fallback: Finding | None = None,
    with_advice: bool = True,
) -> Answer:
    """Answer a question from computed findings, or fall back to the old way.

    ``fallback`` is the finding the router would have shown. Returning its
    summary on any failure means this feature cannot perform worse than the
    routing it replaces.
    """
    # Worked out first, because every fallback path reads it. Computing it
    # lower down left the early returns reading a name that did not exist yet.
    absent = unknown_subjects(question, findings) if question.strip() else []
    if absent:
        log.info("question names %s, absent from the data", absent)

    def _fell_back(reasons: list[str]) -> Answer:
        # Advice is attached here rather than at the end, because several
        # failures return early - unparseable JSON, a missing brace - and those
        # were silently losing the suggestions too. The findings are computed
        # regardless of whether the model managed to word an answer.
        return _with_advice(_bare_fallback(reasons))

    def _bare_fallback(reasons: list[str]) -> Answer:
        if fallback is not None:
            # Labelled as the nearest computed finding rather than presented as
            # an answer. Handing back a sentence about revenue when the
            # question was "explain this to me" and calling it the answer is
            # worse than admitting the question was not understood - it reads
            # as the product ignoring what was asked.
            return Answer(
                f"I could not answer that directly from this data. The closest "
                f"thing already calculated is: {fallback.summary}",
                [fallback.id],
                "engine",
                reasons,
            )
        if absent:
            # Name what is missing. "That is not something this data can
            # answer" is true and unhelpful; the reader asked about candles and
            # deserves to be told there are none, rather than left wondering
            # whether the question was understood.
            missing = ", ".join(absent)
            return Answer(
                f"There is nothing about {missing} in this data, so there is "
                f"nothing to report on it.",
                [],
                "engine",
                reasons,
            )
        return Answer(
            "That is not something this data can answer.", [], "engine", reasons
        )

    def _with_advice(result: Answer) -> Answer:
        # Nothing was asked, so nothing is suggested. suggest() itself accepts
        # a blank question - the story-level summary uses that - but here an
        # empty box should not reach the model at all.
        if with_advice and question.strip():
            result.advice, result.caution = suggest(question, findings, provider)
        return result

    if not question.strip() or not findings:
        return _fell_back(["nothing to answer from"])
    if not provider.available():
        # No model means no answer and no advice; the engine's own findings are
        # still correct, which is what the fallback carries.
        return _fell_back(["no model configured"])

    try:
        raw = provider.complete(
            SYSTEM_PROMPT,
            _build_prompt(question, findings, absent),
            max_tokens=300,
            temperature=0.0,  # this is retrieval and wording, not invention
        )
    except ProviderError as exc:
        log.info("answering unavailable: %s", exc)
        return _fell_back([f"model unavailable: {exc}"])

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return _fell_back(["model did not return JSON"])
    try:
        payload = json.loads(match.group())
    except ValueError:
        return _fell_back(["model returned unparseable JSON"])

    text = str(payload.get("answer", "")).strip()
    used = payload.get("used") or []
    if not isinstance(used, list):
        used = []
    cited_ids = [str(i) for i in used]

    # Ids the model invented are dropped rather than failing the answer
    # outright, so that a real citation alongside a typo still verifies
    # against the real one.
    known = {f.id for f in findings}
    unknown = [i for i in cited_ids if i not in known]
    if unknown:
        log.info("model cited unknown findings: %s", unknown)
    cited_ids = [i for i in cited_ids if i in known]

    problems = verify_answer(text, findings, cited_ids, absent=absent)
    if problems:
        log.warning("rejected answer for %r: %s", question[:60], problems[0])
        return _fell_back(problems)
    return _with_advice(Answer(text, cited_ids, "model"))
