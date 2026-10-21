"""Journal-style tables from a stored analysis run's output (M5.8.1, spec 5.9).

Pure: no database, no model, no data read, nothing recomputed. Values are copied from the recorded output of a locked,
sandboxed `AnalysisRun` and rounded for display only (two decimals, APA style: no leading zero on a correlation).
Output that does not have the shape the generated script prints is refused rather than patched up.
"""

from __future__ import annotations

import math
from typing import Any

NO_VALUE = "–"
DIAGONAL = "—"


class TableError(ValueError):
    """Run output that cannot be turned into a table; the message is shown to the researcher."""


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TableError(f"{where} is not a finite number.")
    return float(value)


def _fixed(value: float, *, leading_zero: bool) -> str:
    text = f"{value:.2f}"
    if text.startswith("-") and float(text) == 0:
        text = text[1:]  # -0.00 would read as a negative result
    if not leading_zero:
        text = text.replace("0.", ".", 1) if text.startswith(("0.", "-0.")) else text
    return text


def descriptives_table(block: Any) -> dict[str, Any]:
    if not isinstance(block, dict) or not block:
        raise TableError("The descriptives output is missing or empty.")
    rows = []
    for name, stats in block.items():
        if not isinstance(stats, dict):
            raise TableError(f"Descriptives for {name!r} are not an object.")
        n = _number(stats.get("n"), f"n for {name!r}")
        sd = stats.get("sd")
        rows.append([
            str(name),
            str(int(n)),
            _fixed(_number(stats.get("mean"), f"mean for {name!r}"), leading_zero=True),
            NO_VALUE if sd is None else _fixed(_number(sd, f"SD for {name!r}"), leading_zero=True),
            _fixed(_number(stats.get("min"), f"min for {name!r}"), leading_zero=True),
            _fixed(_number(stats.get("max"), f"max for {name!r}"), leading_zero=True),
        ])
    return {
        "caption": "Descriptive statistics",
        "columns": ["Variable", "n", "M", "SD", "Min", "Max"],
        "rows": rows,
        "note": "Values are as recorded by the locked analysis run; rounded to two decimals for display.",
    }


def correlation_table(pairs: Any) -> dict[str, Any]:
    if not isinstance(pairs, list) or not pairs:
        raise TableError("The correlations output is missing or empty.")
    variables: list[str] = []
    values: dict[tuple[str, str], float] = {}
    for pair in pairs:
        if not isinstance(pair, dict) or not isinstance(pair.get("a"), str) or not isinstance(pair.get("b"), str):
            raise TableError("A correlation entry has no variable names.")
        a, b = pair["a"], pair["b"]
        r = _number(pair.get("r"), f"r for {a!r} and {b!r}")
        # statistics.correlation can return 1.0000000000000002 for exactly collinear columns; that is rounding, not an error.
        if not -1.0 - 1e-12 <= r <= 1.0 + 1e-12:
            raise TableError(f"r for {a!r} and {b!r} is outside -1 to 1.")
        for name in (a, b):
            if name not in variables:
                variables.append(name)
        values[(a, b)] = r
    rows = []
    for i, row_name in enumerate(variables):
        cells = []
        for j, col_name in enumerate(variables):
            if j > i:
                cells.append("")
            elif j == i:
                cells.append(DIAGONAL)
            else:
                r = values.get((col_name, row_name), values.get((row_name, col_name)))
                cells.append(NO_VALUE if r is None else _fixed(r, leading_zero=False))
        rows.append([row_name, *cells])
    return {
        "caption": "Pearson correlations",
        "columns": ["Variable", *variables],
        "rows": rows,
        "note": "Values are as recorded by the locked analysis run; rounded to two decimals for display.",
    }


def build_tables(output: Any) -> dict[str, dict[str, Any]]:
    """Return one table per test present in a run's output, keyed by test name."""
    if not isinstance(output, dict):
        raise TableError("The run output is not an object.")
    tables: dict[str, dict[str, Any]] = {}
    if "descriptives" in output:
        tables["descriptives"] = descriptives_table(output["descriptives"])
    if "correlations" in output:
        tables["correlations"] = correlation_table(output["correlations"])
    if not tables:
        raise TableError("The run output has no descriptives or correlations to tabulate.")
    return tables
