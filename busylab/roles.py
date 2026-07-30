"""The role vocabulary.

Detection hunts a known vocabulary of roles, not just the three required ones
(spec 3.3). Every role listed here is something the engine knows how to use;
anything outside it is an unknown column, handled separately.

This module is deliberately free of pandas so the vocabulary can be imported
anywhere, including by the API layer, without dragging the engine in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    """A column's meaning, as far as the engine is concerned."""

    DATE = "date"
    PRODUCT = "product"
    REVENUE = "revenue"
    QUANTITY = "quantity"
    UNIT_PRICE = "unit_price"
    COST = "cost"
    CUSTOMER_ID = "customer_id"
    ORDER_ID = "order_id"
    CHANNEL = "channel"
    REGION = "region"
    DISCOUNT = "discount"
    CATEGORY = "category"
    PAYMENT_METHOD = "payment_method"

    # Not detected, only assigned by the user on the confirmation screen.
    GROUP_BY = "group_by"
    MEASURE = "measure"
    IGNORE = "ignore"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Tier(str, Enum):
    """A band of analysis unlocked by having certain roles present."""

    CORE = "core"
    MARGIN = "margin"
    CUSTOMER = "customer"
    SEGMENT = "segment"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class RoleSpec:
    """What a role is, in plain language, and what it buys the business."""

    role: Role
    label: str
    #: Shown on the confirmation screen and on locked-tier prompts.
    description: str
    #: True for roles the engine cannot run without.
    required: bool = False
    #: Analyses that become possible once this role is present.
    unlocks: tuple[str, ...] = ()


#: The canonical description of every role the engine understands.
ROLE_SPECS: dict[Role, RoleSpec] = {
    Role.DATE: RoleSpec(
        role=Role.DATE,
        label="Date",
        description="When the sale happened.",
        required=True,
        unlocks=("trends", "seasonality", "significance", "forecasting"),
    ),
    Role.PRODUCT: RoleSpec(
        role=Role.PRODUCT,
        label="Product",
        description="What was sold.",
        required=True,
        unlocks=("per-product trends", "concentration", "rankings"),
    ),
    Role.REVENUE: RoleSpec(
        role=Role.REVENUE,
        label="Revenue",
        description="What the sale was worth.",
        required=True,
        unlocks=("revenue trends", "concentration", "decomposition"),
    ),
    Role.QUANTITY: RoleSpec(
        role=Role.QUANTITY,
        label="Quantity",
        description="How many units were sold.",
        unlocks=("price vs volume decomposition", "demand forecasting"),
    ),
    Role.UNIT_PRICE: RoleSpec(
        role=Role.UNIT_PRICE,
        label="Unit price",
        description="Price of a single unit.",
        unlocks=("price vs volume decomposition", "revenue derivation"),
    ),
    Role.COST: RoleSpec(
        role=Role.COST,
        label="Cost",
        description="What the goods cost you.",
        unlocks=("contribution margin", "break-even", "profit forecasting"),
    ),
    Role.CUSTOMER_ID: RoleSpec(
        role=Role.CUSTOMER_ID,
        label="Customer ID",
        description="Who bought it.",
        unlocks=("repeat vs new", "retention cohorts", "RFM segmentation"),
    ),
    Role.ORDER_ID: RoleSpec(
        role=Role.ORDER_ID,
        label="Order ID",
        description="Which basket the line belongs to.",
        unlocks=("basket analysis", "order count vs basket size decomposition"),
    ),
    Role.CHANNEL: RoleSpec(
        role=Role.CHANNEL,
        label="Sales channel",
        description="Where the sale happened (online, in store, wholesale).",
        unlocks=("channel segmentation",),
    ),
    Role.REGION: RoleSpec(
        role=Role.REGION,
        label="Region",
        description="Geographic location of the sale.",
        unlocks=("geographic segmentation",),
    ),
    Role.DISCOUNT: RoleSpec(
        role=Role.DISCOUNT,
        label="Discount",
        description="Money taken off the sale.",
        unlocks=("discount effectiveness", "margin give-away analysis"),
    ),
    Role.CATEGORY: RoleSpec(
        role=Role.CATEGORY,
        label="Category",
        description="Product grouping.",
        unlocks=("category rollups",),
    ),
    Role.PAYMENT_METHOD: RoleSpec(
        role=Role.PAYMENT_METHOD,
        label="Payment method",
        description="How the customer paid.",
        unlocks=("payment mix segmentation",),
    ),
}

#: Roles the engine refuses to run without. Revenue may be derived, see below.
REQUIRED_ROLES: frozenset[Role] = frozenset(
    {r for r, spec in ROLE_SPECS.items() if spec.required}
)

#: Roles that are detected from the file rather than assigned by the user.
DETECTABLE_ROLES: tuple[Role, ...] = tuple(ROLE_SPECS)

#: Roles only ever set by hand on the confirmation screen (spec 3.3).
MANUAL_ROLES: tuple[Role, ...] = (Role.GROUP_BY, Role.MEASURE, Role.IGNORE)

#: Roles whose values are categorical labels rather than numbers or dates.
CATEGORICAL_ROLES: frozenset[Role] = frozenset(
    {
        Role.PRODUCT,
        Role.CHANNEL,
        Role.REGION,
        Role.CATEGORY,
        Role.PAYMENT_METHOD,
        Role.CUSTOMER_ID,
        Role.ORDER_ID,
    }
)

#: Roles whose values must parse as numbers.
NUMERIC_ROLES: frozenset[Role] = frozenset(
    {Role.REVENUE, Role.QUANTITY, Role.UNIT_PRICE, Role.COST, Role.DISCOUNT}
)

#: Roles that represent money, as opposed to counts.
MONETARY_ROLES: frozenset[Role] = frozenset(
    {Role.REVENUE, Role.UNIT_PRICE, Role.COST, Role.DISCOUNT}
)


@dataclass(frozen=True)
class TierSpec:
    """A tier of analysis, and the roles that unlock it."""

    tier: Tier
    label: str
    #: All of these must be present.
    requires_all: tuple[Role, ...] = ()
    #: At least one of these must be present.
    requires_any: tuple[Role, ...] = ()
    #: Copy for the locked state in the UI, e.g. "Add a cost column to...".
    locked_prompt: str = ""


TIER_SPECS: dict[Tier, TierSpec] = {
    Tier.CORE: TierSpec(
        tier=Tier.CORE,
        label="Core analysis",
        requires_all=(Role.DATE, Role.PRODUCT, Role.REVENUE),
        locked_prompt="",
    ),
    Tier.MARGIN: TierSpec(
        tier=Tier.MARGIN,
        label="Profit and margin",
        requires_all=(Role.COST,),
        locked_prompt="Add a cost column to unlock profit insights.",
    ),
    Tier.CUSTOMER: TierSpec(
        tier=Tier.CUSTOMER,
        label="Customer intelligence",
        requires_all=(Role.CUSTOMER_ID,),
        locked_prompt="Add a customer ID column to unlock retention and repeat-buyer insights.",
    ),
    Tier.SEGMENT: TierSpec(
        tier=Tier.SEGMENT,
        label="Segmentation",
        requires_any=(Role.CHANNEL, Role.REGION),
        locked_prompt="Add a channel or region column to unlock segmentation.",
    ),
}


def tiers_for(roles: set[Role] | frozenset[Role]) -> dict[Tier, bool]:
    """Return which tiers are unlocked by the roles present.

    Every tier is reported, unlocked or not, because the UI shows locked
    insights greyed out rather than hiding them (spec 3.4).
    """
    present = set(roles)
    result: dict[Tier, bool] = {}
    for tier, spec in TIER_SPECS.items():
        ok = all(r in present for r in spec.requires_all)
        if spec.requires_any:
            ok = ok and any(r in present for r in spec.requires_any)
        result[tier] = ok
    return result


def can_derive_revenue(roles: set[Role] | frozenset[Role]) -> bool:
    """Revenue may be computed from quantity x unit price (spec 3.2)."""
    return Role.QUANTITY in roles and Role.UNIT_PRICE in roles


def missing_required(roles: set[Role] | frozenset[Role]) -> set[Role]:
    """Required roles that are absent and cannot be derived."""
    present = set(roles)
    missing = {r for r in REQUIRED_ROLES if r not in present}
    if Role.REVENUE in missing and can_derive_revenue(present):
        missing.discard(Role.REVENUE)
    return missing
