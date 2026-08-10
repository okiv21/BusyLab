"""Layer 3: agreement, competition, and asking only about the ambiguous.

Keywords propose, content verifies, the user confirms only the ambiguous
(spec 3.2). Concretely:

* Where the name and the values agree, the column is settled and no question
  is ever raised.
* Where the name is confident but the values contradict it, the name loses and
  a question is raised. This is the ``discount_amount``-as-revenue trap.
* Where two columns compete for one role, only those columns are asked about.
* Where nothing matched but the values are usable, the column is offered as a
  generic grouping dimension rather than silently dropped.

A clean file therefore reaches the analysis engine without a single prompt, and
a messy file is only asked about its own mess.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Literal

import pandas as pd

from ..roles import (
    MONETARY_ROLES,
    ROLE_SPECS,
    TIER_SPECS,
    Role,
    Tier,
    can_derive_revenue,
    missing_required,
    tiers_for,
)
from . import content as content_layer
from . import keywords as keyword_layer
from .content import ContentProfile

# How the two layers are blended. The name carries more weight than the values
# because names are usually deliberate, but the values hold a veto.
KEYWORD_WEIGHT = 0.6
CONTENT_WEIGHT = 0.4
#: Added when both layers independently point the same way.
AGREEMENT_BONUS = 0.15
#: Added when no other column is a serious contender for the role, which is
#: what lets a well-formed but badly named column be rescued on content alone.
UNCONTESTED_BONUS = 0.30

#: At or above this, the assignment is made silently.
CONFIDENT = 0.70
#: A rival this close to the winner makes the choice a genuine coin toss.
CONTEST_MARGIN = 0.15
#: Below this, a rival is not really competing.
RIVAL_FLOOR = 0.30

Status = Literal["confident", "ambiguous", "unknown", "conflict"]

#: Columns the loader adds for provenance. They are real groupings (a sheet
#: per month is a real dimension) but the user should never be quizzed on one.
RESERVED_COLUMNS: frozenset[str] = frozenset({"_sheet", "_source", "_ingested_at"})


@dataclass
class RoleCandidate:
    """One role a column might have, and the evidence for it."""

    role: Role
    keyword_score: float
    content_score: float
    combined: float
    agreed: bool
    vetoed: bool = False

    @property
    def label(self) -> str:
        return ROLE_SPECS[self.role].label


@dataclass
class ColumnVerdict:
    """What detection concluded about a single column."""

    column: str
    profile: ContentProfile
    candidates: list[RoleCandidate] = field(default_factory=list)
    role: Role | None = None
    confidence: float = 0.0
    status: Status = "unknown"
    reason: str = ""

    @property
    def top_candidates(self) -> list[RoleCandidate]:
        return sorted(self.candidates, key=lambda c: -c.combined)


@dataclass
class ConfirmationPrompt:
    """A question for the user. Only ambiguous columns produce one.

    ``options`` is ordered best-guess first so the UI can pre-fill it, which is
    what makes the confirmation screen a single click for most files.
    """

    column: str
    question: str
    options: list[Role]
    suggested: Role | None
    reason: str
    #: Extra non-role choices the UI should always offer.
    allow_ignore: bool = True
    allow_group_by: bool = False

    @property
    def suggested_label(self) -> str:
        return ROLE_SPECS[self.suggested].label if self.suggested else "Ignore"


@dataclass
class DetectionResult:
    """Everything the rest of the engine needs to know about a file's shape."""

    assignments: dict[Role, str] = field(default_factory=dict)
    verdicts: list[ColumnVerdict] = field(default_factory=list)
    prompts: list[ConfirmationPrompt] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    tiers: dict[Tier, bool] = field(default_factory=dict)
    missing: set[Role] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    fingerprint: str = ""
    #: "per_unit" or "total". Decides how profit is computed, so it is carried
    #: as data rather than buried in a note.
    cost_basis: str | None = None
    #: True when revenue was verified as quantity x unit price, row by row.
    revenue_is_line_total: bool | None = None

    @property
    def ready(self) -> bool:
        """True when the engine has enough to run without asking anything.

        Required roles must be present *and* nothing ambiguous may be
        outstanding, because publishing an analysis built on a coin toss is
        exactly the confidently-wrong output the spec forbids.
        """
        return not self.missing and not self.blocking_prompts

    @property
    def blocking_prompts(self) -> list[ConfirmationPrompt]:
        """Prompts that must be answered before analysis can be trusted."""
        return [p for p in self.prompts if p.suggested in REQUIRED_OR_VALUABLE]

    @property
    def column_for(self) -> dict[Role, str]:
        return dict(self.assignments)

    def role_of(self, column: str) -> Role | None:
        for role, col in self.assignments.items():
            if col == column:
                return role
        return None

    def locked_tiers(self) -> list[tuple[Tier, str]]:
        """Tiers not yet unlocked, with the prompt that would unlock them."""
        return [
            (tier, TIER_SPECS[tier].locked_prompt)
            for tier, ok in self.tiers.items()
            if not ok and TIER_SPECS[tier].locked_prompt
        ]


#: Roles worth interrupting the user over. Getting these wrong changes the
#: numbers; getting payment_method wrong does not.
REQUIRED_OR_VALUABLE: frozenset[Role] = frozenset(
    {
        Role.DATE,
        Role.PRODUCT,
        Role.REVENUE,
        Role.QUANTITY,
        Role.UNIT_PRICE,
        Role.COST,
        Role.CUSTOMER_ID,
        Role.DISCOUNT,
    }
)


#: Roles that content can recognise as a *shape* but cannot name. Channel,
#: region, category and payment method are all just a few repeated labels, so
#: assigning one of them on content alone would be picking at random. Without
#: keyword support the column is offered as a generic grouping instead, which
#: is the honest answer and the middle path of spec 3.3.
NAME_REQUIRED_ROLES: frozenset[Role] = frozenset(
    {
        Role.CHANNEL,
        Role.REGION,
        Role.CATEGORY,
        Role.PAYMENT_METHOD,
        # Cost and discount belong here for a different reason than the
        # categoricals above, and a sharper one.
        #
        # Content cannot tell one money column from another. Tax, shipping, a
        # service charge, a commission and a genuine cost of goods all profile
        # identically: positive, continuous, two decimal places, smaller than
        # revenue. So with content alone, any stray amount column could be
        # claimed as cost - and cost feeds margin directly, meaning a shipping
        # fee read as cost of goods silently changes every profit number in
        # the story rather than producing a visible error.
        #
        # Quantity, unit price and revenue are deliberately left out: those
        # three are corroborated arithmetically against each other, which is
        # real evidence independent of the name. Nothing corroborates cost.
        Role.COST,
        Role.DISCOUNT,
    }
)


def _build_candidates(profile: ContentProfile, column: str) -> list[RoleCandidate]:
    """Blend the two layers into one score per plausible role."""
    keyword_scores = {m.role: m.score for m in keyword_layer.match_name(column)}
    content_scores = content_layer.score_content(profile)

    candidates: list[RoleCandidate] = []
    for role in set(keyword_scores) | set(content_scores):
        kw = keyword_scores.get(role, 0.0)
        cs = content_scores.get(role, 0.0)

        if role in NAME_REQUIRED_ROLES and kw <= 0.0:
            continue

        # The veto: the values say this role is impossible, so the name is
        # overruled no matter how sure it sounded.
        if cs < content_layer.VETO_THRESHOLD:
            if kw >= 0.6:
                candidates.append(
                    RoleCandidate(role, kw, cs, 0.0, agreed=False, vetoed=True)
                )
            continue

        agreed = kw >= 0.6 and cs >= 0.5
        combined = KEYWORD_WEIGHT * kw + CONTENT_WEIGHT * cs
        if agreed:
            combined += AGREEMENT_BONUS
        candidates.append(
            RoleCandidate(role, kw, cs, min(round(combined, 4), 1.0), agreed)
        )

    return sorted(candidates, key=lambda c: -c.combined)


def _assign(verdicts: list[ColumnVerdict]) -> dict[Role, str]:
    """Greedy global assignment: strongest evidence claims its role first.

    One column holds one role and one role is held by one column, so a strong
    revenue column takes ``revenue`` before a weaker cost column can, and the
    cost column then settles into ``cost`` rather than both fighting over the
    same generic "this is money" content score.
    """
    pairs: list[tuple[float, str, Role]] = []
    for verdict in verdicts:
        for cand in verdict.candidates:
            if cand.combined > 0:
                pairs.append((cand.combined, verdict.column, cand.role))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2].value))

    taken_roles: dict[Role, str] = {}
    taken_columns: set[str] = set()
    for score, column, role in pairs:
        if column in taken_columns or role in taken_roles:
            continue
        taken_roles[role] = column
        taken_columns.add(column)
    return taken_roles


def _rivals_for(
    role: Role, winner: str, verdicts: list[ColumnVerdict]
) -> list[tuple[str, float]]:
    """Other columns making a real claim on the same role."""
    out: list[tuple[str, float]] = []
    for verdict in verdicts:
        if verdict.column == winner:
            continue
        for cand in verdict.candidates:
            if cand.role is role and cand.combined >= RIVAL_FLOOR:
                out.append((verdict.column, cand.combined))
    return sorted(out, key=lambda p: -p[1])


def _uncontested_boost(
    role: Role, winner: str, score: float, verdicts: list[ColumnVerdict]
) -> float:
    rivals = _rivals_for(role, winner, verdicts)
    if not rivals:
        return min(score + UNCONTESTED_BONUS, 1.0)
    return score


def detect(
    df: pd.DataFrame,
    *,
    overrides: dict[str, Role] | None = None,
) -> DetectionResult:
    """Work out what every column in ``df`` means.

    ``overrides`` maps a column name to a role the user has already confirmed,
    which is how answers from the confirmation screen, and remembered mappings
    from a previous run, are fed back in.
    """
    overrides = dict(overrides or {})
    result = DetectionResult()

    # Columns the loader added itself are provenance, not business data, so
    # they are settled before anyone is asked about them.
    for column in df.columns:
        if str(column) in RESERVED_COLUMNS:
            overrides.setdefault(str(column), Role.GROUP_BY)

    verdicts = [
        ColumnVerdict(column=str(col), profile=content_layer.profile_column(df[col], str(col)))
        for col in df.columns
    ]
    for verdict in verdicts:
        verdict.candidates = _build_candidates(verdict.profile, verdict.column)

    by_name = {v.column: v for v in verdicts}

    # User and memory decisions win outright and are removed from contention.
    forced: dict[Role, str] = {}
    for column, role in overrides.items():
        verdict = by_name.get(column)
        if verdict is None:
            continue
        if role in (Role.IGNORE, Role.GROUP_BY, Role.MEASURE):
            verdict.role = role
            verdict.status = "confident"
            verdict.confidence = 1.0
            verdict.reason = "set by the user"
            continue
        forced[role] = column
        verdict.role = role
        verdict.status = "confident"
        verdict.confidence = 1.0
        verdict.reason = "confirmed by the user"

    contested = [v for v in verdicts if v.role is None]
    assignments = _assign(contested)
    assignments.update(forced)

    corroboration = _corroborate(df, assignments)

    # Score and classify each assignment.
    for role, column in list(assignments.items()):
        verdict = by_name[column]
        if verdict.status == "confident" and verdict.role is role:
            continue
        cand = next((c for c in verdict.candidates if c.role is role), None)
        if cand is None:
            continue
        score = _uncontested_boost(role, column, cand.combined, contested)
        score = min(score + corroboration.get(role, 0.0), 1.0)
        verdict.role = role
        verdict.confidence = round(score, 4)

        rivals = _rivals_for(role, column, contested)
        close = [r for r in rivals if cand.combined - r[1] < CONTEST_MARGIN]

        if close:
            verdict.status = "ambiguous"
            names = " and ".join(c for c, _ in close[:2])
            verdict.reason = f"competes with {names} for {ROLE_SPECS[role].label}"
        elif score >= CONFIDENT:
            verdict.status = "confident"
            verdict.reason = (
                "name and values agree"
                if cand.agreed
                else "recognised from the values"
                if cand.keyword_score < 0.3
                else "recognised from the name"
            )
        else:
            verdict.status = "ambiguous"
            verdict.reason = "the evidence is thin"

    # Columns that ended up with nothing.
    for verdict in verdicts:
        if verdict.role is not None:
            continue
        vetoed = [c for c in verdict.candidates if c.vetoed]
        if vetoed:
            verdict.status = "conflict"
            top = vetoed[0]
            verdict.reason = (
                f"named like {ROLE_SPECS[top.role].label} but the values do not match"
            )
        else:
            verdict.status = "unknown"
            verdict.reason = "not recognised"
            result.unknown_columns.append(verdict.column)

    result.verdicts = verdicts
    result.assignments = assignments
    result.prompts = _build_prompts(verdicts, assignments, overrides)

    present = set(assignments)
    result.missing = missing_required(present)
    result.tiers = tiers_for(present)
    if Role.REVENUE not in present and can_derive_revenue(present):
        result.notes.append("Revenue will be computed from quantity x unit price.")
    result.notes.extend(_cross_column_notes(df, assignments))
    result.cost_basis = _cost_basis_kind(df, assignments)
    result.revenue_is_line_total = _revenue_matches_qty_times_price(df, assignments)
    result.fingerprint = schema_fingerprint(df)
    return result


def _build_prompts(
    verdicts: list[ColumnVerdict],
    assignments: dict[Role, str],
    overrides: dict[str, Role],
) -> list[ConfirmationPrompt]:
    """Turn only the genuinely unresolved columns into questions."""
    prompts: list[ConfirmationPrompt] = []

    for verdict in verdicts:
        if verdict.column in overrides:
            continue

        if verdict.status == "ambiguous" and verdict.role is not None:
            options = [
                c.role for c in verdict.top_candidates if not c.vetoed
            ][:3]
            if verdict.role not in options:
                options.insert(0, verdict.role)
            prompts.append(
                ConfirmationPrompt(
                    column=verdict.column,
                    question=(
                        f"This looks like {ROLE_SPECS[verdict.role].label}. "
                        "Is that right?"
                    ),
                    options=options,
                    suggested=verdict.role,
                    reason=verdict.reason,
                )
            )

        elif verdict.status == "conflict":
            vetoed = [c for c in verdict.candidates if c.vetoed]
            suggestion = vetoed[0].role if vetoed else None
            prompts.append(
                ConfirmationPrompt(
                    column=verdict.column,
                    question=(
                        f"\"{verdict.column}\" is named like "
                        f"{ROLE_SPECS[suggestion].label} but does not hold "
                        f"{ROLE_SPECS[suggestion].label.lower()} values. "
                        "What is it?"
                        if suggestion
                        else f"What is \"{verdict.column}\"?"
                    ),
                    options=[],
                    suggested=None,
                    reason=verdict.reason,
                    allow_group_by=True,
                )
            )

        elif verdict.status == "unknown":
            # The middle path of spec 3.3: anything categorical is offered as
            # a generic group-by dimension rather than thrown away.
            if content_layer._score_low_card_label(verdict.profile) >= 0.5:
                prompts.append(
                    ConfirmationPrompt(
                        column=verdict.column,
                        question=(
                            f"\"{verdict.column}\" looks like a label with a few "
                            "repeated values. Compare results across it?"
                        ),
                        options=[],
                        suggested=None,
                        reason="unrecognised but usable as a grouping",
                        allow_group_by=True,
                    )
                )

    return prompts


def _numeric_of(
    df: pd.DataFrame, assignments: dict[Role, str], role: Role
) -> pd.Series | None:
    """The parsed numeric values of whichever column holds ``role``.

    The original index is preserved so columns can be compared row by row.
    """
    from .. import cleaning

    column = assignments.get(role)
    if column is None or column not in df.columns:
        return None
    parsed = cleaning.to_numeric(df[column])
    if parsed.rate < 0.8:
        return None
    vals = parsed.values.dropna()
    return vals if len(vals) else None


def _median_of(df: pd.DataFrame, assignments: dict[Role, str], role: Role) -> float | None:
    """Median of the non-zero values of whichever column holds ``role``."""
    vals = _numeric_of(df, assignments, role)
    if vals is None:
        return None
    nonzero = vals[vals != 0]
    return float(nonzero.median()) if len(nonzero) else None


def _ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series | None:
    """Row-aligned ratio, with zero and missing denominators dropped.

    Comparing medians column by column is wrong: the median of ``qty x price``
    is not the median of ``qty`` times the median of ``price``. Every
    cross-column check has to line up row by row or it will quietly report
    that a perfectly consistent file is inconsistent.
    """
    pair = pd.concat([numerator, denominator], axis=1, join="inner").dropna()
    if pair.empty:
        return None
    left, right = pair.iloc[:, 0], pair.iloc[:, 1]
    mask = right != 0
    if not mask.any():
        return None
    return (left[mask] / right[mask]).replace([float("inf"), float("-inf")], pd.NA).dropna()


def _revenue_matches_qty_times_price(
    df: pd.DataFrame, assignments: dict[Role, str]
) -> bool | None:
    """Does revenue equal quantity x unit price, row by row?"""
    revenue = _numeric_of(df, assignments, Role.REVENUE)
    quantity = _numeric_of(df, assignments, Role.QUANTITY)
    price = _numeric_of(df, assignments, Role.UNIT_PRICE)
    if revenue is None or quantity is None or price is None:
        return None

    implied = pd.concat([quantity, price], axis=1, join="inner").dropna()
    if implied.empty:
        return None
    product = implied.iloc[:, 0] * implied.iloc[:, 1]

    ratios = _ratio(revenue, product)
    if ratios is None or ratios.empty:
        return None
    return bool(0.8 <= float(ratios.median()) <= 1.25)


def _corroborate(df: pd.DataFrame, assignments: dict[Role, str]) -> dict[Role, float]:
    """Confidence earned from other columns rather than from this one.

    A column called ``Amount`` is weak on its own: the name is generic and the
    values are just money, same as cost and discount. But if it also happens to
    equal quantity x unit price, and it is the largest money column in the
    file, that is real arithmetic evidence and it should not have to be
    confirmed by hand. This is where a deterministic engine can be more
    confident than a name-matcher, so it is worth doing.
    """
    boosts: dict[Role, float] = {}

    if _revenue_matches_qty_times_price(df, assignments):
        boosts[Role.REVENUE] = boosts.get(Role.REVENUE, 0.0) + 0.25
        boosts[Role.QUANTITY] = boosts.get(Role.QUANTITY, 0.0) + 0.1
        boosts[Role.UNIT_PRICE] = boosts.get(Role.UNIT_PRICE, 0.0) + 0.1

    # Revenue should be the biggest money column in the file. Cost, discount
    # and unit price all sit below the line total.
    revenue = _median_of(df, assignments, Role.REVENUE)
    if revenue:
        others = [
            v
            for r in (Role.COST, Role.UNIT_PRICE, Role.DISCOUNT)
            if (v := _median_of(df, assignments, r)) is not None
        ]
        if others and revenue >= max(others):
            boosts[Role.REVENUE] = boosts.get(Role.REVENUE, 0.0) + 0.2

    return boosts


def _cross_column_notes(df: pd.DataFrame, assignments: dict[Role, str]) -> list[str]:
    """Checks that only make sense once several columns are known.

    The important one is the cost basis. If a cost column is per-unit but gets
    treated as a line total, every margin in the product is wrong, so it is
    worth resolving arithmetically rather than guessing.
    """
    notes: list[str] = []

    agrees = _revenue_matches_qty_times_price(df, assignments)
    if agrees is True:
        notes.append("Revenue agrees with quantity x unit price.")
    elif agrees is False:
        notes.append(
            "Revenue does not equal quantity x unit price; treating revenue "
            "as the line total."
        )

    basis = _cost_basis(df, assignments)
    if basis:
        notes.append(basis)

    return notes


def _cost_basis_kind(df: pd.DataFrame, assignments: dict[Role, str]) -> str | None:
    """``"per_unit"``, ``"total"``, or None when there is no cost column."""
    note = _cost_basis(df, assignments)
    if note is None:
        return None
    return "per_unit" if "per-unit" in note else "total"


def _cost_basis(df: pd.DataFrame, assignments: dict[Role, str]) -> str | None:
    """Decide whether a cost column is per-unit or a line total.

    Getting this backwards inverts every margin in the product, so it is worth
    settling arithmetically instead of guessing from the column name.

    The test is stability, not size. If cost is *per unit* then
    ``cost x qty / revenue`` is roughly the constant cost ratio while
    ``cost / revenue`` swings with order size. If cost is already a *line
    total* the reverse holds. Whichever hypothesis produces the steadier ratio
    is the right one.
    """
    revenue = _numeric_of(df, assignments, Role.REVENUE)
    quantity = _numeric_of(df, assignments, Role.QUANTITY)
    cost = _numeric_of(df, assignments, Role.COST)
    if revenue is None or cost is None:
        return None

    as_total = _ratio(cost, revenue)
    if quantity is None:
        return "Cost reads as a line total." if as_total is not None else None

    scaled = pd.concat([cost, quantity], axis=1, join="inner").dropna()
    if scaled.empty:
        return None
    as_per_unit = _ratio(scaled.iloc[:, 0] * scaled.iloc[:, 1], revenue)

    def spread(ratios: pd.Series | None) -> float:
        """Coefficient of variation; lower means the hypothesis holds."""
        if ratios is None or len(ratios) < 3:
            return float("inf")
        mean = float(ratios.mean())
        if mean <= 0 or mean > 1.5:
            return float("inf")  # a cost above revenue is not a cost ratio
        return float(ratios.std()) / mean

    per_unit_spread = spread(as_per_unit)
    total_spread = spread(as_total)
    if per_unit_spread == float("inf") and total_spread == float("inf"):
        return None
    if per_unit_spread < total_spread:
        return "Cost reads as a per-unit cost."
    return "Cost reads as a line total."


def schema_fingerprint(df: pd.DataFrame) -> str:
    """A stable hash of a source's shape, for mapping memory (spec 4.1).

    Covers column names, their order and their broad types. A refresh whose
    fingerprint matches a stored one runs silently; a change means schema
    drift, and only the changed columns need re-confirming.
    """
    parts: list[str] = []
    for col in df.columns:
        profile = content_layer.profile_column(df[col], str(col))
        if profile.datetime_rate >= 0.9:
            kind = "date"
        elif profile.numeric_rate >= 0.9:
            kind = "number"
        else:
            kind = "text"
        parts.append(f"{keyword_layer.normalize(col)}:{kind}")
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def describe(result: DetectionResult) -> str:
    """A short plain-text summary. Handy in tests and at the REPL."""
    lines = ["Detected columns:"]
    for role, column in sorted(result.assignments.items(), key=lambda kv: kv[0].value):
        verdict = next(v for v in result.verdicts if v.column == column)
        lines.append(
            f"  {ROLE_SPECS[role].label:<14} <- {column!r}"
            f"  [{verdict.status}, {verdict.confidence:.2f}] {verdict.reason}"
        )
    if result.unknown_columns:
        lines.append(f"Unknown: {', '.join(result.unknown_columns)}")
    if result.prompts:
        lines.append(f"Questions ({len(result.prompts)}):")
        for prompt in result.prompts:
            lines.append(f"  {prompt.column}: {prompt.question}")
    else:
        lines.append("Questions: none")
    if result.missing:
        lines.append(f"MISSING REQUIRED: {', '.join(r.value for r in result.missing)}")
    for note in result.notes:
        lines.append(f"Note: {note}")
    return "\n".join(lines)
