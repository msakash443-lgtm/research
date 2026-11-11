"""Deterministic M7.4 journal matching over researcher-supplied metadata only."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JournalCandidate:
    name: str
    scope_terms: tuple[str, ...]
    indexes: tuple[str, ...]
    apc: float | None
    review_days: int | None
    source_url: str
    checked_at: str
    official_predatory: bool = False


def rank(candidates: list[JournalCandidate], *, scope: list[str], required_index: str | None = None,
         max_apc: float | None = None, max_review_days: int | None = None) -> list[dict]:
    wanted = {term.casefold() for term in scope if term.strip()}
    rows = []
    for candidate in candidates:
        terms = {term.casefold() for term in candidate.scope_terms}
        overlap = len(wanted & terms)
        flags = []
        if required_index and required_index.casefold() not in {item.casefold() for item in candidate.indexes}:
            flags.append("missing_index")
        if max_apc is not None and candidate.apc is not None and candidate.apc > max_apc:
            flags.append("apc_over_budget")
        if max_review_days is not None and candidate.review_days is not None and candidate.review_days > max_review_days:
            flags.append("review_too_slow")
        if candidate.official_predatory:
            flags.append("official_predatory_list")
        rows.append({"name": candidate.name, "scope_overlap": overlap, "flags": flags,
                     "source_url": candidate.source_url, "checked_at": candidate.checked_at})
    return sorted(rows, key=lambda row: (-row["scope_overlap"], row["name"]))
