"""Rule-based response-quality monitoring (M5.2, spec 5.8.2). Pure: no I/O, no model, no participant data leaves here.

It flags rows; it never removes them. Dropping a flagged case is a separate step that needs an approved exclusion
rule (M5.7.1), so a flag is a prompt for a person to look, not a decision.

Checks: attention checks (a named column must hold an expected answer), straight-lining (a long run of identical
answers within the grid columns the caller names), speeders (completion faster than a threshold, measured from the
start column the caller names), submit-before-start timestamps, and duplicates (identical answers to every question).

The caller must say which columns are grid items and which column holds a real start time. Nothing is guessed from
column names: a check whose inputs are not given does not run, and the report says so in `checks`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.survey_google_forms import META_COLUMNS

DEFAULT_MIN_SECONDS = 60.0
DEFAULT_STRAIGHTLINE_MIN_ITEMS = 5


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def check(
    columns: list[str],
    rows: list[list[str]],
    *,
    attention: dict[str, str] | None = None,
    min_seconds: float = DEFAULT_MIN_SECONDS,
    straightline_min_items: int = DEFAULT_STRAIGHTLINE_MIN_ITEMS,
    grid_columns: list[str] | None = None,
    start_column: str | None = None,
) -> dict[str, Any]:
    """Return counts and the flags per response. Refuses inputs it cannot check rather than guessing."""
    if len(set(columns)) != len(columns):
        raise ValueError("Column names must be unique to run quality checks.")
    if straightline_min_items < 2:
        raise ValueError("straightline_min_items must be at least 2.")
    if min_seconds < 0:
        raise ValueError("min_seconds cannot be negative.")
    attention = attention or {}
    for column in attention:
        if column not in columns:
            raise ValueError(f"Attention-check column not found: {column!r}")
    for column in grid_columns or []:
        if column not in columns:
            raise ValueError(f"Grid column not found: {column!r}")
        if column in attention or column in META_COLUMNS:
            raise ValueError(f"Grid column must be a plain question column: {column!r}")
    if start_column is not None and start_column not in columns:
        raise ValueError(f"Start-time column not found: {start_column!r}")
    index = {name: i for i, name in enumerate(columns)}
    question_idx = [i for i, name in enumerate(columns) if name not in META_COLUMNS]
    # Straight-lining runs only over the columns the caller names as grid items. Not named, not run.
    grid_idx = [index[name] for name in (grid_columns or [])]
    created_i = index[start_column] if start_column is not None else None
    submitted_i = index.get("submitted_at")
    id_i = index.get("response_id")

    flags: dict[int, list[str]] = {}

    def flag(row_no: int, reason: str) -> None:
        flags.setdefault(row_no, []).append(reason)

    seen_answers: dict[tuple[str, ...], int] = {}
    for row_no, row in enumerate(rows):
        for column, expected in attention.items():
            if row[index[column]].strip() != expected.strip():
                flag(row_no, f"attention_failed:{column}")

        if grid_idx:
            answered = [row[i].strip() for i in grid_idx if row[i].strip()]
            if len(answered) >= straightline_min_items and len(set(answered)) == 1:
                flag(row_no, "straight_lined")

        if created_i is not None and submitted_i is not None:
            start, end = _parse_time(row[created_i]), _parse_time(row[submitted_i])
            if start and end:
                if (start.tzinfo is None) != (end.tzinfo is None):
                    raise ValueError("Timestamps mix a time zone with none; use one format for both time columns.")
                elapsed = (end - start).total_seconds()
                if elapsed < 0:
                    flag(row_no, "time_order_invalid")
                elif elapsed < min_seconds:
                    flag(row_no, "speeder")

        key = tuple(row[i] for i in question_idx)
        if key in seen_answers:
            flag(row_no, "duplicate_answers")
        else:
            seen_answers[key] = row_no

    out_flags = [
        {"response_id": rows[n][id_i] if id_i is not None else str(n + 1), "row": n + 1, "reasons": reasons}
        for n, reasons in sorted(flags.items())
    ]
    summary = {"attention_failed": 0, "straight_lined": 0, "speeder": 0, "duplicate_answers": 0, "time_order_invalid": 0}
    for entry in out_flags:
        for reason in entry["reasons"]:
            key = reason.split(":", 1)[0]
            if key == "attention_failed":
                summary["attention_failed"] += 1
            elif key in summary:
                summary[key] += 1
    return {
        "row_count": len(rows),
        "flagged_count": len(out_flags),
        "summary": summary,
        "flags": out_flags,
        "checks": {
            "straight_lining": bool(grid_columns),
            "speeder": start_column is not None,
        },
        "removes_rows": False,
    }
