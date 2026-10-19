"""Programmatic-number checks for one manuscript section (M6.3). Pure: plain facts in, a report out, no database or
network.

A section's ProseMirror text inserts a computed statistic with a `stat` node: an atom with `analysis_run_id` and
`path` attrs. It cannot be typed into, only inserted from one of the project's `AnalysisRun` outputs (mirrors how
`cite` works for sources — see `editor/schema.js`). `path` is a dot-separated walk through the run's `output_json`
("descriptives.age.mean", "correlations.0.r").

- `unknown_run`: a `stat` node names an analysis run this project does not have (wrong id, deleted, or it belongs
  to another project).
- `unresolved_number`: the run exists but `path` does not reach a real number in its stored output.

Spec principle 4 / plan rule 24: "numbers come from code, not language" — a value that fails either check never
became a computed value, so it is reported here, never silently dropped or rounded into something that looks
plausible (`app.manuscript_guard` turns these findings into an export block, the same way M6.4 blocks citations).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.claim_linker import Finding

__all__ = ["RunFacts", "NumberReport", "stat_refs_in_order", "resolve_number", "check_section", "flatten_numbers"]


@dataclass(frozen=True)
class RunFacts:
    id: str
    output: dict[str, Any] | None


@dataclass
class NumberReport:
    findings: list[Finding] = field(default_factory=list)
    refs: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def stat_refs_in_order(content: dict[str, Any]) -> list[tuple[str, str]]:
    """(analysis_run_id, path) pairs in the text, each once, in order of first use. Walks nested nodes."""
    seen: dict[tuple[str, str], None] = {}
    stack: list[dict[str, Any]] = [content]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("type") == "stat":
            attrs = node.get("attrs") or {}
            run_id, path = attrs.get("analysis_run_id"), attrs.get("path")
            if isinstance(run_id, str) and run_id and isinstance(path, str) and path:
                seen.setdefault((run_id, path), None)
        children = node.get("content") or []
        stack.extend(reversed([c for c in children if isinstance(c, dict)]))
    return list(seen)


def resolve_number(output: Any, path: str) -> int | float | None:
    """Walk `path` ("a.b.0.c") through `output`; the leaf must be a real int/float (not bool), else None."""
    if not isinstance(output, dict) or not path:
        return None
    node: Any = output
    for part in path.split("."):
        if isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        elif isinstance(node, list):
            if not part.lstrip("-").isdigit():
                return None
            index = int(part)
            if index < 0 or index >= len(node):
                return None
            node = node[index]
        else:
            return None
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None
    return node


def flatten_numbers(output: Any, prefix: str = "") -> list[tuple[str, int | float]]:
    """Every (dot-path, value) pair reachable in `output` whose leaf is a real int/float (not bool) — the same set
    of leaves `resolve_number` can resolve a path to. Dict keys and list indices walk in their stored order (M6.3.1:
    what the Write step's number panel offers, so a listed path always resolves)."""
    results: list[tuple[str, int | float]] = []
    if isinstance(output, dict):
        for key, value in output.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            results.extend(flatten_numbers(value, path))
    elif isinstance(output, list):
        for index, value in enumerate(output):
            path = f"{prefix}.{index}" if prefix else str(index)
            results.extend(flatten_numbers(value, path))
    elif isinstance(output, (int, float)) and not isinstance(output, bool):
        results.append((prefix, output))
    return results


def check_section(content: dict[str, Any], runs: dict[str, RunFacts]) -> NumberReport:
    report = NumberReport()
    refs = stat_refs_in_order(content)
    report.refs = refs
    for run_id, path in refs:
        run = runs.get(run_id)
        if run is None:
            report.findings.append(Finding("unknown_run", run_id, "Inserted number names an analysis run this project does not have"))
        elif resolve_number(run.output, path) is None:
            report.findings.append(
                Finding("unresolved_number", f"{run_id}:{path}", f"Inserted number's path {path!r} is not a number in that run's output")
            )
    return report
