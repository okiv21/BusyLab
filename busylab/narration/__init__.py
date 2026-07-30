"""Narration and routing: the only places a language model is involved.

The model writes sentences and picks which pre-built analysis answers a
question. It never computes, never decides and never produces a number
(spec 2). Everything here degrades to deterministic behaviour when no model is
configured, so BusyLab is fully usable with no API key.
"""

from .narrate import (
    NarrationResult,
    allowed_numbers,
    cache_key,
    invented_numbers,
    narrate,
    narrate_all,
    verify,
)
from .provider import (
    GroqProvider,
    NullProvider,
    Provider,
    ProviderError,
    from_env,
)
from .routing import (
    ROUTES,
    Route,
    RoutingDecision,
    answer_from_findings,
    available_routes,
    route_question,
    suggest_chips,
)

__all__ = [
    "GroqProvider",
    "NarrationResult",
    "NullProvider",
    "Provider",
    "ProviderError",
    "ROUTES",
    "Route",
    "RoutingDecision",
    "allowed_numbers",
    "answer_from_findings",
    "available_routes",
    "cache_key",
    "from_env",
    "invented_numbers",
    "narrate",
    "narrate_all",
    "route_question",
    "suggest_chips",
    "verify",
]
