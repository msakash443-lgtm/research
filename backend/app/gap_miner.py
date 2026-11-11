"""Validation and deterministic ranking rules for M3.9 literature gaps."""

from __future__ import annotations

from collections.abc import Sequence
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.coverage_matrix import DIMENSIONS, Paper, build_matrix, papers_for_project
from app.models import Project, Source
from app.contradictions import detect
from app.dedupe import normalise_title
from typing import Any

GAP_TYPES = frozenset({"theoretical", "methodological", "empirical", "contextual", "population", "temporal"})


def validate_gap_evidence(paper_ids: Sequence[str], cell_ids: Sequence[str]) -> None:
    if len(set(paper_ids)) < 2 and not cell_ids:
        raise ValueError("a gap needs at least two supporting papers or matrix evidence")


def validate_gap_type(gap_type: str) -> None:
    if gap_type not in GAP_TYPES:
        raise ValueError(f"type must be one of: {', '.join(sorted(GAP_TYPES))}")


def validate_project_evidence(db: Session, project: Project, paper_ids: Sequence[uuid.UUID], cell_ids: Sequence[str]) -> None:
    """Reject evidence that is not an active source or a real project matrix cell."""
    if paper_ids:
        found = set(db.scalars(select(Source.id).where(
            Source.project_id == project.id, Source.id.in_(paper_ids), Source.merged_into.is_(None)
        )))
        if found != set(paper_ids):
            raise ValueError("supporting papers must be active sources in this project")
    if cell_ids:
        papers = papers_for_project(db, project)
        valid: set[str] = set()
        dimensions = list(DIMENSIONS)
        for rows, cols in ((dimensions[0], dimensions[1]), (dimensions[0], dimensions[2]), (dimensions[1], dimensions[2])):
            for layer in (None, ({d for d in dimensions} - {rows, cols}).pop()):
                matrix = build_matrix(papers, rows, cols, layer)
                for item in matrix["matrices"]:
                    valid.update(c["cell_id"] for c in item["cells"])
                    valid.update(c["cell_id"] for c in item["empty_cells"])
        if set(cell_ids) - valid:
            raise ValueError("matrix evidence must identify a real cell in this project's coverage matrix")


MAX_CANDIDATES = 50
_CELL_PAIRS = (("method", "population"), ("method", "country"), ("population", "country"))


def _statements(matrix: dict[str, Any]) -> list[dict[str, str]]:
    """Verified `future_research` and `limitations` values with the paper and extraction they came from.
    Same rule as the contradiction check: a person verified the paper and the field carries a quote."""
    out: list[dict[str, str]] = []
    for paper in matrix["papers"]:
        if not paper["verified_by_human"] or not paper["extraction_id"]:
            continue
        for field in ("future_research", "limitations"):
            cell = paper["cells"].get(field, {})
            values = cell.get("value")
            if not cell.get("quote") or not isinstance(values, list):
                continue
            for text in values:
                if isinstance(text, str) and text.strip():
                    out.append({"field": field, "text": " ".join(text.split()), "source_id": paper["source_id"],
                                "extraction_id": paper["extraction_id"], "title": paper["title"]})
    return out


def _candidate(kind: str, gap_type: str, reason: str, paper_ids: list[str], cell_ids: list[str],
               extraction_ids: list[str], future_research: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "kind": kind, "type": gap_type, "reason": reason, "paper_ids": sorted(set(paper_ids)),
        "cell_ids": cell_ids, "extraction_ids": sorted(set(extraction_ids)), "future_research": future_research or [],
    }


def _is_postable(candidate: dict[str, Any]) -> bool:
    """A candidate is returned only if `POST …/gaps` would accept it (same validators)."""
    try:
        validate_gap_type(candidate["type"])
        validate_gap_evidence(candidate["paper_ids"], candidate["cell_ids"])
    except ValueError:
        return False
    return True


def candidates(*, papers: list[Paper], matrix: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic gap candidates, strongest first, capped at MAX_CANDIDATES. No AI, nothing stored.

    Order: contradictions (X.197 conflicts, then inconsistencies), themes that at least two verified papers
    state in the same words, cells holding one paper, then empty cells. A candidate that `POST …/gaps`
    would reject is never returned.
    """
    contradictions = detect(matrix)
    contradiction_items: list[dict[str, Any]] = []
    for kind_reason, groups in (
        ("Verified papers report opposite effects of {e} on {o}.", contradictions["conflicts"]),
        ("One verified paper reports an effect of {e} on {o}; another reports none.", contradictions["inconsistent"]),
    ):
        for group in groups:
            first, second = group["findings"][0], group["findings"][1]
            reason = kind_reason.format(e=group["exposure"], o=group["outcome"])
            contradiction_items.append(_candidate(
                "contradiction", "empirical", reason, [first["source_id"], second["source_id"]], [],
                [first["extraction_id"], second["extraction_id"]],
            ))

    themes: dict[tuple[str, str], dict[str, dict[str, str]]] = {}
    for statement in _statements(matrix):
        key = (statement["field"], normalise_title(statement["text"]))
        if key[1]:
            themes.setdefault(key, {}).setdefault(statement["source_id"], statement)
    theme_items: list[dict[str, Any]] = []
    for (field, _), per_paper in sorted(themes.items()):
        if len(per_paper) < 2:
            continue
        members = sorted(per_paper.values(), key=lambda s: s["source_id"])
        text = members[0]["text"]
        if field == "future_research":
            reason = f"{len(members)} verified papers name the same future-research theme: \"{text}\"."
            gap_type = "empirical"
            prefill = [{"statement": s["text"], "source_id": s["source_id"], "extraction_id": s["extraction_id"]} for s in members]
        else:
            reason = f"{len(members)} verified papers name the same limitation: \"{text}\"."
            gap_type, prefill = "methodological", []
        theme_items.append(_candidate(
            "theme", gap_type, reason, [s["source_id"] for s in members], [],
            [s["extraction_id"] for s in members], prefill,
        ))
    theme_items.sort(key=lambda c: (-len(c["paper_ids"]), c["reason"]))

    thin_items: list[dict[str, Any]] = []
    empty_items: list[dict[str, Any]] = []
    for rows, cols in _CELL_PAIRS:
        view = build_matrix(papers, rows, cols)["matrices"][0]
        for cell in view["cells"]:
            if cell["count"] == 1:
                only = cell["papers"][0]
                thin_items.append(_candidate(
                    "thin_cell", "empirical",
                    f"Only one extracted paper ({only['title']}) covers {rows}={cell['row']}, {cols}={cell['col']}.",
                    [only["source_id"]], [cell["cell_id"]], [only["extraction_id"]],
                ))
        for cell in view["empty_cells"]:
            empty_items.append(_candidate(
                "empty_cell", "empirical",
                f"No extracted paper in this project covers {rows}={cell['row']}, {cols}={cell['col']}.",
                [], [cell["cell_id"]], [],
            ))
    thin_items.sort(key=lambda c: c["cell_ids"])
    empty_items.sort(key=lambda c: c["cell_ids"])

    ordered = contradiction_items + theme_items + thin_items + empty_items
    return [c for c in ordered if _is_postable(c)][:MAX_CANDIDATES]
