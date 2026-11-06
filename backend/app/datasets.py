"""Dataset versions (M5.1): CSV ingestion, SHA-256 hashing, and logged, reversible cleaning.

Pure: no database and no model. A raw dataset is stored exactly as uploaded and is never edited; cleaning always
produces a new version that points at its parent and records each step. The parent stays available, so going back
is just using the earlier version. Participant data is processed here only by these deterministic steps.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
from typing import Any

MAX_ROWS = 50_000
CLEANING_OPS = ("trim_whitespace", "drop_blank_rows", "drop_columns", "pseudonymise_columns")
PSEUDONYM_PREFIX = "P-"
PSEUDONYM_HEX_CHARS = 16


def pseudonym(value: str, key: bytes) -> str:
    """Keyed pseudonym for one cell value (M5.6). Same key and value give the same pseudonym, so rows still link;
    without the key the mapping cannot be recomputed."""
    digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return PSEUDONYM_PREFIX + digest[:PSEUDONYM_HEX_CHARS]


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse(payload: bytes) -> tuple[str, list[str], list[list[str]]]:
    """Return the exact text, the header and the rows. Refuse anything that isn't a clean rectangular table.

    A leading UTF-8 byte-order mark is dropped, and truly blank lines (before the header or between rows) are
    skipped. A row of empty cells is not blank here; drop_blank_rows handles that as a cleaning step."""
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("The file must be UTF-8 text.") from exc
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next((row for row in reader if row), None)
    if header is None:
        raise ValueError("The file is empty; it needs a header row.")
    if any(not name.strip() for name in header):
        raise ValueError("Every column needs a name in the header row.")
    if len(set(header)) != len(header):
        raise ValueError("Column names in the header must be unique.")
    rows: list[list[str]] = []
    for row in reader:
        if not row:
            continue
        if len(rows) >= MAX_ROWS:
            raise ValueError(f"A dataset may have at most {MAX_ROWS} rows.")
        if len(row) != len(header):
            raise ValueError(f"Row {reader.line_num} has {len(row)} cells; the header has {len(header)}.")
        rows.append(row)
    return text, header, rows


def serialise(columns: list[str], rows: list[list[str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return out.getvalue()


def apply_cleaning(
    columns: list[str],
    rows: list[list[str]],
    steps: list[dict[str, Any]],
    pseudonym_key: bytes | None = None,
) -> tuple[list[str], list[list[str]], list[dict[str, Any]]]:
    """Apply the steps in order; return the new table and one log entry per step. The log never holds cell values."""
    log: list[dict[str, Any]] = []
    for step in steps:
        op = step.get("op")
        before = len(rows)
        if op == "trim_whitespace":
            rows = [[cell.strip() for cell in row] for row in rows]
            detail = "Removed leading and trailing spaces from every cell."
        elif op == "drop_blank_rows":
            rows = [row for row in rows if any(cell.strip() for cell in row)]
            detail = "Removed rows in which every cell is blank."
        elif op == "drop_columns":
            names = step.get("columns") or []
            if not names:
                raise ValueError("drop_columns needs the column names to drop.")
            unknown = [name for name in names if name not in columns]
            if unknown:
                raise ValueError(f"Unknown column(s): {', '.join(unknown)}.")
            keep = [i for i, name in enumerate(columns) if name not in names]
            if not keep:
                raise ValueError("Cleaning cannot remove every column.")
            columns = [columns[i] for i in keep]
            rows = [[row[i] for i in keep] for row in rows]
            detail = f"Dropped column(s): {', '.join(names)}."
        elif op == "pseudonymise_columns":
            names = step.get("columns") or []
            if not names:
                raise ValueError("pseudonymise_columns needs the column names to pseudonymise.")
            if not pseudonym_key:
                raise ValueError("Pseudonymisation needs a server key; none is configured.")
            unknown = [name for name in names if name not in columns]
            if unknown:
                raise ValueError(f"Unknown column(s): {', '.join(unknown)}.")
            targets = [i for i, name in enumerate(columns) if name in names]
            # Blank cells stay blank: an empty answer is information, not an identifier to pseudonymise.
            rows = [
                [pseudonym(cell, pseudonym_key) if i in targets and cell != "" else cell for i, cell in enumerate(row)]
                for row in rows
            ]
            detail = f"Pseudonymised column(s) with a keyed hash: {', '.join(names)}."
        else:
            raise ValueError(f"Unknown cleaning step: {op!r}. Allowed: {', '.join(CLEANING_OPS)}.")
        log.append({"op": op, "detail": detail, "rows_before": before, "rows_after": len(rows)})
    return columns, rows, log
