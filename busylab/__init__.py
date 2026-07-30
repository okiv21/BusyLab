"""BusyLab analysis engine.

A pure Python library. It runs standalone against a file on disk with no web
stack, no server and no deployment involved (spec 9, "the one non-negotiable").
The API is a thin wrapper around this package, never the other way round.
"""

from .roles import Role, Tier, missing_required, tiers_for

__version__ = "0.1.0"

__all__ = ["Role", "Tier", "missing_required", "tiers_for", "__version__"]
