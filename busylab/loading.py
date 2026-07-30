"""Getting a usable table out of a real spreadsheet.

Detection assumes a rectangular frame whose first row is the header. Real SME
workbooks are not that (spec 3.1): there is a title and a logo above the
header, the header is split across two merged rows, there is a "TOTAL" row
halfway down, there are blank spacer columns, and there is one tab per month.

Everything here is structural repair only. It never touches the meaning of a
column, which is detection's job, and it never drops a row it cannot justify.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import cleaning

#: Words that mark a summary row rather than a transaction.
TOTAL_MARKERS: frozenset[str] = frozenset(
    {
        "total",
        "totals",
        "subtotal",
        "sub total",
        "sub-total",
        "grand total",
        "grandtotal",
        "sum",
        "summary",
        "average",
        "avg",
        "mean",
        "balance",
        "closing balance",
        "count",
        "overall",
    }
)

#: How far down the sheet to look for the real header row.
MAX_HEADER_SCAN = 12


@dataclass
class LoadReport:
    """What the loader had to do, so the UI can be honest about it."""

    source: str = ""
    sheets_used: list[str] = field(default_factory=list)
    header_row: int | None = None
    multi_row_header: bool = False
    rows_in: int = 0
    rows_out: int = 0
    dropped_total_rows: int = 0
    dropped_blank_rows: int = 0
    dropped_blank_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = [f"{self.rows_out} rows"]
        if len(self.sheets_used) > 1:
            bits.append(f"{len(self.sheets_used)} sheets combined")
        if self.dropped_total_rows:
            bits.append(f"{self.dropped_total_rows} total rows removed")
        if self.multi_row_header:
            bits.append("multi-row header merged")
        return " · ".join(bits)


def _cell_is_blank(value: object) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    if pd.isna(value) if not isinstance(value, str) else False:
        return True
    return str(value).strip().lower() in cleaning.NULL_TOKENS


def _row_blank_share(row: pd.Series) -> float:
    if len(row) == 0:
        return 1.0
    return sum(_cell_is_blank(v) for v in row) / len(row)


def _looks_like_header(row: pd.Series) -> float:
    """Score how much a row behaves like a header rather than data.

    Headers are mostly filled, mostly short text, and rarely numeric.
    """
    values = [v for v in row if not _cell_is_blank(v)]
    if not values:
        return 0.0
    filled = len(values) / len(row)
    texty = sum(isinstance(v, str) and not _is_numberish(v) for v in values) / len(values)
    short = sum(len(str(v).strip()) <= 40 for v in values) / len(values)
    distinct = len({str(v).strip().lower() for v in values}) / len(values)
    return filled * 0.35 + texty * 0.3 + short * 0.15 + distinct * 0.2


def _is_numberish(value: object) -> bool:
    text = str(value).strip()
    if not text:
        return False
    token, _ = cleaning._clean_number_token(text)
    return bool(cleaning._NUMERIC_SHAPE.match(token))


def find_header_row(raw: pd.DataFrame) -> int:
    """Index of the row that is most plausibly the column header."""
    best_index, best_score = 0, -1.0
    limit = min(MAX_HEADER_SCAN, len(raw))
    for i in range(limit):
        row = raw.iloc[i]
        score = _looks_like_header(row)
        # A header is followed by data, so the next row should look less
        # header-like than it does. This is what separates a header from a
        # title banner sitting above it.
        if i + 1 < len(raw):
            following = _looks_like_header(raw.iloc[i + 1])
            if following > score:
                score *= 0.5
        if score > best_score:
            best_index, best_score = i, score
    return best_index


def _merge_header_rows(upper: pd.Series, lower: pd.Series) -> list[str]:
    """Combine a merged two-row header into single names.

    Merged cells arrive with the label in the first column of the merge and
    blanks after it, so the upper row is forward-filled before joining.
    """
    filled = upper.ffill()
    names: list[str] = []
    for top, bottom in zip(filled, lower):
        top_text = "" if _cell_is_blank(top) else str(top).strip()
        bottom_text = "" if _cell_is_blank(bottom) else str(bottom).strip()
        if top_text and bottom_text and top_text.lower() != bottom_text.lower():
            names.append(f"{top_text} {bottom_text}")
        else:
            names.append(bottom_text or top_text)
    return names


def _dedupe(names: list[str]) -> list[str]:
    """Make column names unique and non-empty without inventing meaning."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for i, raw in enumerate(names):
        name = str(raw).strip()
        if not name or name.lower().startswith("unnamed"):
            name = f"column_{i + 1}"
        key = name.lower()
        if key in seen:
            seen[key] += 1
            name = f"{name}_{seen[key]}"
        else:
            seen[key] = 1
        out.append(name)
    return out


def _is_total_row(row: pd.Series) -> bool:
    """True for a summary row sitting inside the data.

    Requires a marker word in a cell that is *mostly* that word, so a product
    genuinely called "Total Care Soap" is not mistaken for a subtotal.
    """
    for value in row:
        if not isinstance(value, str):
            continue
        text = value.strip().lower().rstrip(":")
        if text in TOTAL_MARKERS:
            return True
        for marker in ("grand total", "sub total", "sub-total", "subtotal", "total"):
            if text.startswith(marker) and len(text) <= len(marker) + 12:
                return True
    return False


def tidy(raw: pd.DataFrame, report: LoadReport | None = None) -> pd.DataFrame:
    """Turn a raw grid into a clean rectangular table.

    ``raw`` must have been read with ``header=None`` so the loader can decide
    for itself where the header is.
    """
    report = report if report is not None else LoadReport()
    report.rows_in = int(len(raw))

    frame = raw.dropna(how="all").dropna(axis=1, how="all")
    if frame.empty:
        return pd.DataFrame()
    frame = frame.reset_index(drop=True)

    header_index = find_header_row(frame)
    report.header_row = header_index

    header = frame.iloc[header_index]
    body_start = header_index + 1

    # A second header row is one that is still header-like and whose row below
    # is not, or one that fills gaps left by merged cells above it.
    names: list[str]
    if body_start < len(frame):
        candidate = frame.iloc[body_start]
        upper_gaps = sum(_cell_is_blank(v) for v in header)
        if upper_gaps > 0 and _looks_like_header(candidate) >= 0.45:
            names = _merge_header_rows(header, candidate)
            body_start += 1
            report.multi_row_header = True
        else:
            names = ["" if _cell_is_blank(v) else str(v).strip() for v in header]
    else:
        names = ["" if _cell_is_blank(v) else str(v).strip() for v in header]

    body = frame.iloc[body_start:].copy()
    body.columns = _dedupe(names)
    body = body.reset_index(drop=True)

    # Remove summary rows before anything measures anything.
    total_mask = body.apply(_is_total_row, axis=1)
    report.dropped_total_rows = int(total_mask.sum())
    body = body[~total_mask]

    blank_mask = body.apply(lambda r: _row_blank_share(r) >= 0.999, axis=1)
    report.dropped_blank_rows = int(blank_mask.sum())
    body = body[~blank_mask]

    # Spacer columns: entirely empty under a real header.
    empty_columns = [
        col for col in body.columns if _row_blank_share(body[col]) >= 0.999
    ]
    if empty_columns:
        report.dropped_blank_columns = [str(c) for c in empty_columns]
        body = body.drop(columns=empty_columns)

    body = body.reset_index(drop=True)
    report.rows_out = int(len(body))
    return body


def load_excel(
    path: str | Path,
    *,
    sheet: str | int | None = None,
    combine_sheets: bool = True,
) -> tuple[pd.DataFrame, LoadReport]:
    """Read a workbook into one tidy frame.

    Many businesses keep one tab per month. When the tabs share a shape they
    are concatenated into a single table with a ``_sheet`` column recording
    where each row came from, because a year of sales split across twelve tabs
    is one dataset, not twelve.
    """
    path = Path(path)
    report = LoadReport(source=path.name)

    sheets = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
    if isinstance(sheets, pd.DataFrame):
        sheets = {str(sheet) if sheet is not None else "Sheet1": sheets}

    tidied: dict[str, pd.DataFrame] = {}
    for name, grid in sheets.items():
        sub = LoadReport(source=path.name)
        frame = tidy(grid, sub)
        if frame.empty or len(frame.columns) < 2:
            continue
        tidied[str(name)] = frame
        report.rows_in += sub.rows_in
        report.dropped_total_rows += sub.dropped_total_rows
        report.dropped_blank_rows += sub.dropped_blank_rows
        report.multi_row_header = report.multi_row_header or sub.multi_row_header
        if report.header_row is None:
            report.header_row = sub.header_row

    if not tidied:
        report.notes.append("No usable table found in this file.")
        return pd.DataFrame(), report

    if len(tidied) == 1 or not combine_sheets:
        name, frame = next(iter(tidied.items()))
        report.sheets_used = [name]
        report.rows_out = int(len(frame))
        return frame, report

    groups = _group_by_shape(tidied)
    largest = max(groups, key=lambda g: sum(len(tidied[n]) for n in g))
    if len(largest) < len(tidied):
        skipped = [n for n in tidied if n not in largest]
        report.notes.append(
            "Skipped sheets with a different layout: " + ", ".join(skipped)
        )

    parts = []
    for name in largest:
        part = tidied[name].copy()
        part["_sheet"] = name
        parts.append(part)

    combined = pd.concat(parts, ignore_index=True, sort=False)
    report.sheets_used = list(largest)
    report.rows_out = int(len(combined))
    if len(largest) > 1:
        report.notes.append(f"Combined {len(largest)} sheets into one table.")
    return combined, report


def _group_by_shape(frames: dict[str, pd.DataFrame]) -> list[list[str]]:
    """Cluster sheets that share a column signature, so only alike tabs merge."""
    buckets: dict[tuple[str, ...], list[str]] = {}
    for name, frame in frames.items():
        key = tuple(sorted(str(c).strip().lower() for c in frame.columns))
        buckets.setdefault(key, []).append(name)
    return list(buckets.values())


def load(path: str | Path, **kwargs) -> tuple[pd.DataFrame, LoadReport]:
    """Read any supported file into a tidy frame."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return load_excel(path, **kwargs)
    if suffix in {".csv", ".tsv", ".txt"}:
        sep = "\t" if suffix == ".tsv" else None
        raw = pd.read_csv(path, header=None, dtype=object, sep=sep, engine="python")
        report = LoadReport(source=path.name, sheets_used=["csv"])
        frame = tidy(raw, report)
        return frame, report
    raise ValueError(f"Unsupported file type: {path.suffix}")
