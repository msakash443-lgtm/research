"""Case-exclusion rules (M5.7.1, spec 5.8 gate: approve exclusion rules before applying; never silently drop cases).

Pure: no database and no model. A rule is proposed, then a human approves it, and only then can a cleaning step apply
it. `apply` refuses any rule whose content no longer matches the hash recorded at approval, and it drops rows only for
approved rules. Each dropped batch is logged with the rule id and hash, so every exclusion traces to a decision.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

OPS = ("equals", "not_equals", "is_blank", "less_than", "greater_than")
NUMERIC_OPS = ("less_than", "greater_than")
VALUE_OPS = ("equals", "not_equals") + NUMERIC_OPS


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def normalise(rule: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    """Return the rule in canonical form, or raise ValueError. Only the fields the op needs are kept."""
    column = rule.get("column")
    op = rule.get("op")
    if column not in columns:
        raise ValueError(f"Unknown column: {column!r}.")
    if op not in OPS:
        raise ValueError(f"Unknown rule operator: {op!r}. Allowed: {', '.join(OPS)}.")
    if op == "is_blank":
        if rule.get("value") not in (None, ""):
            raise ValueError("is_blank takes no value.")
        return {"column": column, "op": op}
    value = rule.get("value")
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{op} needs a value.")
    if op in NUMERIC_OPS and _number(value) is None:
        raise ValueError(f"{op} needs a numeric value.")
    return {"column": column, "op": op, "value": value}


def rule_sha256(rule: dict[str, Any]) -> str:
    canonical = json.dumps(rule, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def matches(rule: dict[str, Any], column_index: int, row: list[str]) -> bool:
    cell = row[column_index]
    op = rule["op"]
    if op == "is_blank":
        return cell.strip() == ""
    if op == "equals":
        return cell == rule["value"]
    if op == "not_equals":
        return cell != rule["value"]
    # Numeric rules never match a cell they cannot read as a number, so unclear cells are kept, not dropped.
    number, target = _number(cell), _number(rule["value"])
    if number is None or target is None:
        return False
    return number < target if op == "less_than" else number > target


def apply(
    columns: list[str], rows: list[list[str]], approved: list[dict[str, Any]]
) -> tuple[list[list[str]], list[dict[str, Any]]]:
    """Apply approved rules in order. `approved` items hold {"id", "rule", "rule_sha256"}; any mismatch is refused."""
    ids = [entry["id"] for entry in approved]
    if len(set(ids)) != len(ids):
        raise ValueError("The same exclusion rule was listed more than once.")
    log: list[dict[str, Any]] = []
    for entry in approved:
        rule = entry["rule"]
        if rule_sha256(rule) != entry["rule_sha256"]:
            raise ValueError("An exclusion rule no longer matches the hash recorded when it was approved.")
        index = columns.index(rule["column"])
        before = len(rows)
        rows = [row for row in rows if not matches(rule, index, row)]
        dropped = before - len(rows)
        log.append({
            "op": "exclude_rows",
            "rule_id": entry["id"],
            "rule_sha256": entry["rule_sha256"],
            "rule": rule,
            "rows_before": before,
            "rows_after": len(rows),
            "detail": f"Excluded {dropped} row(s) matching an approved rule.",
        })
    return rows, log
