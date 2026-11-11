"""Seed-based false-exclusion check (plan M2.9.1; spec 11 "false-exclusion rate < 2%", 7.7).

Seed papers are papers the scholar already knows belong in the review, so every seed the AI pre-screen
would exclude is a false exclusion. This module is pure: it matches seeds to the project's stored sources
and turns the per-seed answers into counts. It calls no model and touches no database.

What the number is, and is not:
* only a rough check. It has no gold "exclude" papers, so it cannot give kappa (that needs X.15's
  hand-screened set), and with fewer than `MIN_SEEDS_FOR_LIMIT` checked seeds a single miss already
  exceeds the 2% limit while a clean result proves little;
* a "maybe" is not a false exclusion (a person still sees the paper), the same rule as `evaluation.py`;
* a seed that matches no stored source, or cannot be identified, is reported and left out of the rate;
* an AI failure on any seed makes the whole result `incomplete`, never `below_limit`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from app.dedupe import doi_key, match_kind, work_key
from app.evaluation import FALSE_EXCLUSION_LIMIT

MIN_SEEDS_FOR_LIMIT = math.ceil(1 / FALSE_EXCLUSION_LIMIT)  # 50: below this one miss is already >= the limit

NOT_IN_SOURCES = "not_in_sources"
CANNOT_CHECK = "cannot_check"
ANSWERED = "answered"
FAILED = "failed"
AI_LOCKED = "ai_locked"  # the matched source is set to "never for this item": not sent to the model

NO_SEEDS = "no_seeds"
NOTHING_TO_CHECK = "nothing_to_check"
INCOMPLETE = "incomplete"
BELOW_LIMIT = "below_limit"
AT_OR_ABOVE_LIMIT = "at_or_above_limit"


@dataclass(frozen=True)
class SeedMatch:
    seed: Any
    source: Any | None  # the matching stored source, or None
    status: str | None  # None = can be checked; otherwise NOT_IN_SOURCES or CANNOT_CHECK
    matched_by: str | None  # "doi" | "title_year_author"


def _identifiable(seed: Any) -> bool:
    return bool(doi_key(seed.doi)) or work_key(seed) is not None


def match_seeds(seeds: Iterable[Any], sources: Sequence[Any]) -> list[SeedMatch]:
    """Pair each seed with the first stored source that is the same work (`dedupe.match_kind`: the same DOI,
    else the same title + year + first author; two different DOIs never match)."""
    matches: list[SeedMatch] = []
    for seed in seeds:
        if not _identifiable(seed):
            matches.append(SeedMatch(seed, None, CANNOT_CHECK, None))
            continue
        found = next(((source, kind) for source in sources if (kind := match_kind(seed, source))), None)
        if found is None:
            matches.append(SeedMatch(seed, None, NOT_IN_SOURCES, None))
        else:
            matches.append(SeedMatch(seed, found[0], None, found[1]))
    return matches


def summarise(entries: Sequence[dict[str, Any]], *, limit: float = FALSE_EXCLUSION_LIMIT) -> dict[str, Any]:
    """Counts and the rate from per-seed entries (`status`, and for answered ones `decision` and
    `auto_exclude_eligible`). The rate is excluded / answered; the limit is compared on the exact fraction."""
    by_status = lambda s: [e for e in entries if e["status"] == s]  # noqa: E731
    answered = by_status(ANSWERED)
    excluded = [e for e in answered if e["decision"] == "exclude"]
    failed = by_status(FAILED)
    # An incomplete run reports no rate: a figure over only the seeds that answered would look like a result.
    rate = len(excluded) / len(answered) if answered and not failed else None

    notes = ["A rough check, not proof: it uses only known-relevant seed papers, so it cannot give kappa."]
    if not entries:
        status = NO_SEEDS
        notes.append("Add seed papers first (Criteria & seeds).")
    elif not answered and not failed:
        status = NOTHING_TO_CHECK
        notes.append("No seed matches a stored source, so there is nothing to screen.")
    elif failed:
        status = INCOMPLETE
        notes.append(f"The AI step failed on {len(failed)} seed(s); no rate is judged until every matched seed is answered.")
    else:
        status = AT_OR_ABOVE_LIMIT if rate >= limit else BELOW_LIMIT
    if answered and len(answered) < MIN_SEEDS_FOR_LIMIT:
        notes.append(
            f"Only {len(answered)} seed(s) checked: with fewer than {MIN_SEEDS_FOR_LIMIT}, one false exclusion "
            f"already reaches {limit:.0%}, and zero says little about the model."
        )
    if by_status(NOT_IN_SOURCES):
        notes.append(f"{len(by_status(NOT_IN_SOURCES))} seed(s) match no stored source (not retrieved or not added) and were not tested.")
    if by_status(CANNOT_CHECK):
        notes.append(f"{len(by_status(CANNOT_CHECK))} seed(s) cannot be matched: add a DOI or a fuller title.")
    if by_status(AI_LOCKED):
        notes.append(f"{len(by_status(AI_LOCKED))} seed(s) match a source set to 'never for this item' and were not sent to the model, so the rate leaves them out.")

    return {
        "seeds": len(entries),
        "checked": len(answered),
        "excluded": len(excluded),
        "included": sum(1 for e in answered if e["decision"] == "include"),
        "maybe": sum(1 for e in answered if e["decision"] == "maybe"),
        "failed": len(failed),
        "not_in_sources": len(by_status(NOT_IN_SOURCES)),
        "cannot_check": len(by_status(CANNOT_CHECK)),
        "ai_locked": len(by_status(AI_LOCKED)),
        "auto_exclude_eligible": sum(1 for e in excluded if e.get("auto_exclude_eligible")),
        "false_exclusion_rate": round(rate, 3) if rate is not None else None,
        "limit": limit,
        "status": status,
        "rough": True,
        "notes": notes,
    }


__all__ = [
    "AT_OR_ABOVE_LIMIT", "BELOW_LIMIT", "INCOMPLETE", "MIN_SEEDS_FOR_LIMIT", "NO_SEEDS", "NOTHING_TO_CHECK",
    "ANSWERED", "FAILED", "NOT_IN_SOURCES", "CANNOT_CHECK", "AI_LOCKED", "SeedMatch", "match_seeds", "summarise",
]
