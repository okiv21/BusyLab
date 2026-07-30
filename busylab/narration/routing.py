"""Routing a question to a pre-built analysis.

Spec 6 describes this as the hard, important part of feeling open while staying
honest. The user types "why did that happen" or "what about Lagos" and gets a
real answer, but the answer is always produced by a computation the engine
already knows how to run. The model chooses *which* analysis; it never performs
one and never produces a number.

That constraint is what makes free-form questions safe. A model asked to
analyse would confabulate; a model asked to pick from a list of twelve named
analyses is doing classification, which small fast models do reliably. When it
picks something not on the list, or when there is no model at all, keyword
matching takes over and the worst case is an honest "I cannot answer that from
this data".
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from ..findings import Finding
from .provider import Provider, ProviderError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Route:
    """One question the engine knows how to answer."""

    name: str
    #: Shown to the user as a guided follow-up chip (spec 6).
    label: str
    #: Shown to the model so it can match intent to computation.
    intent: str
    #: Words that should reach this route with no model involved.
    keywords: tuple[str, ...] = ()
    #: Finding ids that must exist for this route to be offerable.
    requires_findings: tuple[str, ...] = ()
    #: Canonical column names that must exist in the data.
    requires_columns: tuple[str, ...] = ()


#: The vocabulary of answerable questions. Every entry maps to a computation
#: that already exists, which is what keeps the drill-down honest.
ROUTES: tuple[Route, ...] = (
    Route(
        name="why_change",
        label="Why did this change?",
        intent="Explain what drove a rise or fall in revenue, broken down by product.",
        keywords=("why", "cause", "reason", "driver", "what happened", "explain"),
        requires_findings=("revenue_decomposition",),
    ),
    Route(
        name="break_down_by_channel",
        label="Break down by channel",
        intent="Show how the change splits across sales channels, online versus in store.",
        keywords=("channel", "online", "in store", "wholesale", "where"),
        requires_columns=("channel",),
        requires_findings=("decomposition_channel", "segmentation_channel"),
    ),
    Route(
        name="break_down_by_region",
        label="Break down by location",
        intent="Show how results differ by region, state or city.",
        keywords=("region", "location", "city", "state", "lagos", "abuja", "branch"),
        requires_columns=("region",),
        requires_findings=("decomposition_region", "segmentation_region"),
    ),
    Route(
        name="price_or_volume",
        label="Was it price or volume?",
        intent="Say whether a revenue change came from units sold or from price per unit.",
        keywords=("price", "volume", "units", "quantity", "cheaper", "expensive"),
        requires_findings=("price_volume_split",),
    ),
    Route(
        name="compare_last_year",
        label="Compare to last year",
        intent="Compare the current period against the same months a year earlier.",
        keywords=("last year", "year ago", "previous year", "seasonal", "same period"),
    ),
    Route(
        name="always_like_this",
        label="Was it always like this?",
        intent="Show the full history of this measure to see whether the pattern is new.",
        keywords=("always", "history", "before", "used to", "trend", "over time"),
    ),
    Route(
        name="most_profitable",
        label="Which products actually make money?",
        intent="Rank products by profit and margin rather than by revenue.",
        keywords=(
            "profit",
            "margin",
            "make money",
            "makes money",
            "making money",
            "profitable",
            "earn",
            "cost",
        ),
        requires_columns=("profit",),
        requires_findings=("margin_reality", "loss_making_product"),
    ),
    Route(
        name="concentration",
        label="How dependent am I on one product?",
        intent="Show how much of the total sits in the largest one or two products.",
        keywords=("depend", "concentration", "risk", "reliant", "biggest share"),
        requires_findings=("concentration",),
    ),
    Route(
        name="which_customers",
        label="Which customers changed?",
        intent="Split the change into repeat customers versus new customers.",
        keywords=("customer", "who", "repeat", "new", "loyal", "churn", "lost"),
        requires_columns=("customer_id",),
    ),
    Route(
        name="week_by_week",
        label="Show me week by week",
        intent="Show the measure at weekly resolution to locate when a change started.",
        keywords=("week", "weekly", "when did", "started", "day"),
    ),
    Route(
        name="what_sells_together",
        label="What sells together?",
        intent="Show which products move together across periods.",
        keywords=("together", "pair", "bundle", "cross sell", "correlat", "also buy"),
        requires_findings=("product_relationships",),
    ),
    Route(
        name="whats_coming",
        label="Where is this heading?",
        intent="Show the forecast and its uncertainty range for this measure.",
        keywords=("forecast", "predict", "next month", "future", "heading", "expect"),
    ),
)

ROUTES_BY_NAME: dict[str, Route] = {r.name: r for r in ROUTES}

#: Returned when nothing matches. Saying so is better than answering wrongly.
UNANSWERABLE = "unanswerable"


@dataclass
class RoutingDecision:
    """Which analysis a question was sent to, and how sure we are."""

    route: Route | None
    confidence: float
    source: str  # "model", "keywords" or "none"
    question: str = ""
    alternatives: list[Route] = field(default_factory=list)

    @property
    def answerable(self) -> bool:
        return self.route is not None

    @property
    def refusal(self) -> str:
        """What to show the user when nothing can answer the question."""
        return (
            "That is not something this data can answer. "
            "Try one of the suggested questions instead."
        )


def available_routes(
    findings: list[Finding], columns: set[str] | None = None
) -> list[Route]:
    """The routes that can actually be answered from this dataset.

    Offering a chip the engine cannot answer is worse than offering fewer
    chips, so availability is computed from what was actually produced.
    """
    columns = columns or set()
    found = {f.id for f in findings}

    out: list[Route] = []
    for route in ROUTES:
        if not route.requires_columns and not route.requires_findings:
            out.append(route)
            continue

        # Either kind of evidence is enough. A finding that already exists
        # proves the capability just as well as the raw column does, and
        # requiring both would silently drop answerable questions whenever a
        # caller did not happen to pass the column set.
        by_column = bool(route.requires_columns) and set(route.requires_columns) <= columns
        by_finding = bool(route.requires_findings) and bool(
            set(route.requires_findings) & found
        )
        if by_column or by_finding:
            out.append(route)
    return out


def suggest_chips(
    findings: list[Finding], columns: set[str] | None = None, limit: int = 5
) -> list[Route]:
    """Guided follow-up chips: specific vetted questions, not a filter panel.

    Spec 6 is explicit that the engine offers the next question rather than
    handing the user a blank box, because a blank box hands the analytical work
    back to them.
    """
    return available_routes(findings, columns)[:limit]


def _keyword_route(question: str, routes: list[Route]) -> tuple[Route | None, float]:
    """Match on words alone. The fallback when there is no model."""
    text = question.lower()
    best: Route | None = None
    best_score = 0.0
    for route in routes:
        hits = sum(1 for kw in route.keywords if kw in text)
        if not hits:
            continue
        # Longer keyword matches are more specific and so more trustworthy.
        specificity = max((len(kw) for kw in route.keywords if kw in text), default=0)
        score = hits + specificity / 100
        if score > best_score:
            best, best_score = route, score
    if best is None:
        return None, 0.0
    return best, min(0.4 + best_score * 0.15, 0.75)


SYSTEM_PROMPT = """You match a question to one analysis from a fixed list.

You never answer the question. You never calculate anything. You never \
produce a number. You only choose which analysis should run.

Reply with JSON only: {"route": "<name>", "confidence": <0 to 1>}

Use "unanswerable" if no analysis on the list fits the question."""


def _model_route(
    question: str, routes: list[Route], provider: Provider
) -> tuple[Route | None, float] | None:
    """Ask the model to classify. Returns None if it could not be used."""
    catalogue = "\n".join(f"- {r.name}: {r.intent}" for r in routes)
    prompt = (
        f"Available analyses:\n{catalogue}\n\n"
        f'Question: "{question}"\n\n'
        "Which analysis answers this?"
    )
    try:
        raw = provider.complete(SYSTEM_PROMPT, prompt, max_tokens=60, temperature=0.0)
    except ProviderError as exc:
        log.info("routing model unavailable: %s", exc)
        return None

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group())
    except ValueError:
        return None

    name = str(payload.get("route", "")).strip()
    try:
        confidence = float(payload.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    if name == UNANSWERABLE:
        return None, 0.0
    # The model may only pick from the list it was given. Anything else is a
    # hallucinated capability and is discarded.
    allowed = {r.name: r for r in routes}
    if name not in allowed:
        log.warning("model proposed unknown route %r", name)
        return None
    return allowed[name], max(0.0, min(confidence, 1.0))


def route_question(
    question: str,
    findings: list[Finding],
    provider: Provider,
    *,
    columns: set[str] | None = None,
) -> RoutingDecision:
    """Send a natural language question to a pre-built analysis.

    The model interprets intent; the engine does the work. If neither the model
    nor the keywords can place the question, the decision says so rather than
    guessing, because a confidently wrong answer is the one outcome this
    architecture exists to avoid.
    """
    routes = available_routes(findings, columns)
    if not routes or not question.strip():
        return RoutingDecision(None, 0.0, "none", question)

    if provider.available():
        outcome = _model_route(question, routes, provider)
        if outcome is not None:
            route, confidence = outcome
            if route is not None:
                return RoutingDecision(
                    route,
                    confidence,
                    "model",
                    question,
                    alternatives=[r for r in routes if r is not route][:3],
                )
            # The model explicitly said nothing fits; trust that over keywords.
            return RoutingDecision(None, 0.0, "model", question, alternatives=routes[:3])

    route, confidence = _keyword_route(question, routes)
    return RoutingDecision(
        route,
        confidence,
        "keywords" if route else "none",
        question,
        alternatives=[r for r in routes if r is not route][:3],
    )


def answer_from_findings(decision: RoutingDecision, findings: list[Finding]) -> Finding | None:
    """The already-computed finding that answers a routed question.

    Drill-down is answered from analysis that has already run (spec 6), so this
    is a lookup rather than a fresh computation.
    """
    if decision.route is None:
        return None
    wanted = set(decision.route.requires_findings)
    for finding in findings:
        if finding.id in wanted:
            return finding

    # Fall back to a type-based match for routes with no fixed finding id.
    by_route: dict[str, tuple[str, ...]] = {
        "break_down_by_channel": ("decomposition_channel", "segmentation_channel"),
        "break_down_by_region": ("decomposition_region", "segmentation_region"),
        "most_profitable": ("margin_reality", "loss_making_product"),
        "always_like_this": ("revenue_trend", "seasonality"),
        "compare_last_year": ("seasonality", "revenue_trend"),
        "week_by_week": ("revenue_trend",),
        "concentration": ("concentration",),
        "what_sells_together": ("product_relationships",),
        "why_change": ("revenue_decomposition", "decomposition_channel"),
        "price_or_volume": ("price_volume_split",),
    }
    for candidate in by_route.get(decision.route.name, ()):
        for finding in findings:
            if finding.id == candidate:
                return finding
    return None
