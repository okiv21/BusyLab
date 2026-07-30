"""Column detection: keywords propose, content verifies, the user confirms.

See spec 3.2. The three layers live in :mod:`keywords`, :mod:`content` and
:mod:`engine` respectively.
"""

from .content import ContentProfile, profile_column, score_content
from .engine import (
    ColumnVerdict,
    ConfirmationPrompt,
    DetectionResult,
    RoleCandidate,
    describe,
    detect,
    schema_fingerprint,
)
from .keywords import KeywordMatch, best_match, match_name, normalize

__all__ = [
    "ColumnVerdict",
    "ConfirmationPrompt",
    "ContentProfile",
    "DetectionResult",
    "KeywordMatch",
    "RoleCandidate",
    "best_match",
    "describe",
    "detect",
    "match_name",
    "normalize",
    "profile_column",
    "schema_fingerprint",
    "score_content",
]
