"""Value coercion for real-world SME spreadsheets.

"Proper file format" narrows the mess, it does not remove it (spec 3.1). Cells
arrive as ``"₦1,234.50"``, ``"(500)"``, ``"12%"``, ``"n/a"``, ``"15/03/2026"``
and Excel serial numbers, often in the same column. Everything here is
best-effort and reports how well it did, because the parse *rate* is itself the
evidence Layer 2 uses to decide what a column is.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

#: Strings that mean "no value" regardless of what the column holds.
NULL_TOKENS: frozenset[str] = frozenset(
    {
        "",
        "-",
        "--",
        "---",
        "n/a",
        "na",
        "nan",
        "nil",
        "none",
        "null",
        "#n/a",
        "#value!",
        "#div/0!",
        "#ref!",
        "tbd",
        "unknown",
        "?",
        ".",
    }
)

_CURRENCY_CHARS = "₦$£€¥"
_NUM_STRIP = re.compile(rf"[{_CURRENCY_CHARS}\s,'’]")
_LEADING_CURRENCY_WORD = re.compile(
    r"^(ngn|naira|usd|gbp|eur|kes|ghs|zar|n)\s*(?=[\d(.-])", re.IGNORECASE
)
_PAREN_NEGATIVE = re.compile(r"^\((.*)\)$")
_NUMERIC_SHAPE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")

#: Excel stores dates as days since 1899-12-30. Anything in this window is a
#: plausible date serial rather than a quantity or a price.
_EXCEL_EPOCH = pd.Timestamp("1899-12-30")
_EXCEL_SERIAL_MIN = 20000  # 1954
_EXCEL_SERIAL_MAX = 60000  # 2064


@dataclass(frozen=True)
class ParseResult:
    """A coerced series plus how much of it actually parsed."""

    values: pd.Series
    #: Fraction of non-null input cells that survived coercion, 0.0 to 1.0.
    rate: float
    #: Set when the column was recognised as percentages and rescaled.
    was_percent: bool = False
    #: Set when values were read as Excel date serial numbers.
    was_excel_serial: bool = False
    #: Set when dates were read day-first (15/03) rather than month-first.
    dayfirst: bool | None = None


def blank_mask(series: pd.Series) -> pd.Series:
    """True where a cell is empty or holds a placeholder like "n/a"."""
    if series.dtype.kind in "biufcM":
        return series.isna()
    text = series.astype("string").str.strip().str.lower()
    return text.isna() | text.isin(NULL_TOKENS)


def non_null(series: pd.Series) -> pd.Series:
    """The series with blanks and placeholder tokens removed."""
    return series[~blank_mask(series)]


def _clean_number_token(text: str) -> tuple[str, bool]:
    """Strip currency and grouping noise. Returns (token, is_percent)."""
    token = text.strip()
    is_percent = token.endswith("%")
    if is_percent:
        token = token[:-1].strip()
    token = _LEADING_CURRENCY_WORD.sub("", token)
    negative = False
    paren = _PAREN_NEGATIVE.match(token)
    if paren:
        negative = True
        token = paren.group(1)
    token = _NUM_STRIP.sub("", token)
    if token.endswith("-"):  # trailing-minus accounting style
        negative = True
        token = token[:-1]
    if negative and token and not token.startswith("-"):
        token = "-" + token
    return token, is_percent


def to_numeric(series: pd.Series) -> ParseResult:
    """Coerce a column to numbers, surviving currency symbols and commas.

    Percentages are detected as a column-level property: if most of the values
    carry a ``%`` sign they are all divided by 100, so a discount column reads
    as 0.15 rather than 15.
    """
    values = non_null(series)
    if values.empty:
        return ParseResult(
            pd.to_numeric(pd.Series(dtype="float64")), 0.0
        )

    if values.dtype.kind in "biuf":
        return ParseResult(pd.to_numeric(values, errors="coerce"), 1.0)

    tokens: list[str] = []
    percent_flags: list[bool] = []
    for raw in values.astype(str):
        token, is_percent = _clean_number_token(raw)
        tokens.append(token if _NUMERIC_SHAPE.match(token) else "")
        percent_flags.append(is_percent)

    parsed = pd.to_numeric(pd.Series(tokens, index=values.index), errors="coerce")
    rate = float(parsed.notna().mean()) if len(parsed) else 0.0

    percent_share = float(np.mean(percent_flags)) if percent_flags else 0.0
    was_percent = percent_share >= 0.5
    if was_percent:
        parsed = parsed / 100.0

    return ParseResult(parsed, rate, was_percent=was_percent)


def to_datetime(series: pd.Series) -> ParseResult:
    """Coerce a column to timestamps.

    Tries day-first and month-first and keeps whichever parses more cells,
    which is how ``15/03/2026`` and ``03/15/2026`` both end up correct without
    asking anyone. Excel serial numbers are handled separately because they
    arrive as plain integers.
    """
    values = non_null(series)
    if values.empty:
        return ParseResult(pd.Series(dtype="datetime64[ns]"), 0.0)

    if values.dtype.kind == "M":
        return ParseResult(values, 1.0)

    # Numeric column: possibly Excel serials.
    if values.dtype.kind in "biuf":
        numeric = pd.to_numeric(values, errors="coerce")
        in_window = numeric.between(_EXCEL_SERIAL_MIN, _EXCEL_SERIAL_MAX)
        share = float(in_window.mean()) if len(numeric) else 0.0
        if share >= 0.9:
            converted = _EXCEL_EPOCH + pd.to_timedelta(numeric, unit="D")
            return ParseResult(converted, share, was_excel_serial=True)
        return ParseResult(pd.Series(dtype="datetime64[ns]"), 0.0)

    # Numbers held as text are still numbers. Without this, a column read from
    # Excel as object dtype lets pandas parse "6500.0" as the year 6500, which
    # both invents a date column and overflows on arithmetic. A column that
    # parses cleanly as numbers can only be a date via the Excel serial route,
    # which was handled above.
    numeric_rate = to_numeric(values).rate
    if numeric_rate >= 0.9:
        numeric = to_numeric(values).values
        in_window = numeric.between(_EXCEL_SERIAL_MIN, _EXCEL_SERIAL_MAX)
        share = float(in_window.mean()) if len(numeric) else 0.0
        if share >= 0.9:
            converted = _EXCEL_EPOCH + pd.to_timedelta(numeric, unit="D")
            return ParseResult(converted, share, was_excel_serial=True)
        return ParseResult(pd.Series(dtype="datetime64[ns]"), 0.0)

    text = values.astype(str).str.strip()
    best: ParseResult | None = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for dayfirst in (True, False):
            parsed = pd.to_datetime(
                text, errors="coerce", dayfirst=dayfirst, format="mixed"
            )
            rate = float(parsed.notna().mean()) if len(parsed) else 0.0
            if best is None or rate > best.rate:
                best = ParseResult(parsed, rate, dayfirst=dayfirst)

    assert best is not None
    # When both orderings parse equally well the dates are unambiguous
    # (day > 12 never appeared), so the flag carries no information.
    return best


def to_text(series: pd.Series) -> pd.Series:
    """Normalised text values with blanks removed, for cardinality checks."""
    values = non_null(series)
    if values.empty:
        return pd.Series(dtype="object")
    return values.astype(str).str.strip()


def looks_numeric(series: pd.Series, threshold: float = 0.9) -> bool:
    """True if the column is convincingly numeric after cleaning."""
    return to_numeric(series).rate >= threshold


def looks_temporal(series: pd.Series, threshold: float = 0.9) -> bool:
    """True if the column is convincingly a date after cleaning."""
    return to_datetime(series).rate >= threshold
