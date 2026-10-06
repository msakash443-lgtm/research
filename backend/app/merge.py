"""Merge duplicate records into one without losing anything (spec 5.2.2, plan M1.6.3).

`merge_records(group)` takes the records `app.dedupe.group_duplicates` put together (anchor
first) and returns one `PaperRecord` plus a `MergeResult` that says what was combined and why.
Pure and deterministic; it neither reads nor writes the database.

Rules:
* **Nothing is dropped.** Every member's `connector`/`external_id` ends up in `source_ids`
  (e.g. `{"openalex": "W1", "crossref": "10.1000/abc", "semantic_scholar": "..."}`), as do the
  ids the members already carried. When two members give a *different* id under the same key
  the anchor's wins and the other is written to the decision log, so it can still be found.
* **DOI:** the first one present. Members that carry different DOIs are never merged
  (`MergeError`), the same rule `dedupe.match_kind` applies.
* **Abstract:** the longest (the most complete), ties to the earlier record. **Authors:** the
  longest list. **Title, year, venue, url, open-access url:** the first value present, so the
  anchor decides unless it has none.
* **Flags:** the union, then re-derived where a merge changes the truth: `no_abstract` is
  dropped once any member supplied an abstract (or added if none did) and `invalid_doi` is
  dropped once a valid DOI is known.
* **Decision log:** every field where members disagreed, or where a non-anchor member supplied
  the value, gets a `MergeDecision` naming who was chosen and who was passed over. Long text
  is described by length, not copied, so the log stays small and can be audited without
  spreading third-party text around.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.connectors.base import PaperRecord
from app.dedupe import doi_key, match_kind


class MergeError(ValueError):
    """The records must not be merged (empty group, or conflicting DOIs)."""


@dataclass(frozen=True)
class MergeDecision:
    field: str
    chosen_from: str  # "connector:external_id" of the member whose value was used
    reason: str  # first_present | longest | id_conflict
    passed_over: tuple[str, ...] = ()  # "connector:external_id (summary)" for members that disagreed


@dataclass(frozen=True)
class MergeResult:
    record: PaperRecord
    members: tuple[str, ...]  # "connector:external_id", anchor first
    matched_by: dict[str, str]  # member -> "anchor" | "doi" | "title_year_author" | "group"
    decisions: tuple[MergeDecision, ...]

    def audit_payload(self) -> dict[str, Any]:
        """Ids and short descriptions only (no abstracts/titles), for `audit.record`."""
        return {
            "members": list(self.members),
            "matched_by": self.matched_by,
            "decisions": [
                {"field": d.field, "chosen_from": d.chosen_from, "reason": d.reason, "passed_over": list(d.passed_over)}
                for d in self.decisions
            ],
        }


def _ref(record: PaperRecord) -> str:
    return f"{record.connector}:{record.external_id}"


def _summary(field: str, value: Any) -> str:
    if field == "abstract":
        return f"{len(value)} chars"
    if field == "authors":
        return f"{len(value)} authors"
    return str(value)[:80]


def _first_present(field: str, records: Sequence[PaperRecord], decisions: list[MergeDecision]) -> Any:
    holders = [(r, getattr(r, field)) for r in records if getattr(r, field)]
    if not holders:
        return None
    chosen, value = holders[0]
    others = tuple(f"{_ref(r)} ({_summary(field, v)})" for r, v in holders[1:] if v != value)
    if others or chosen is not records[0]:
        decisions.append(MergeDecision(field, _ref(chosen), "first_present", others))
    return value


def _longest(field: str, records: Sequence[PaperRecord], decisions: list[MergeDecision]) -> Any:
    holders = [(r, getattr(r, field)) for r in records if getattr(r, field)]
    if not holders:
        return None
    chosen, value = holders[0]
    for record, candidate in holders[1:]:
        if len(candidate) > len(value):  # strictly longer, so ties stay with the earlier record
            chosen, value = record, candidate
    others = tuple(f"{_ref(r)} ({_summary(field, v)})" for r, v in holders if r is not chosen and v != value)
    if others or chosen is not records[0]:
        decisions.append(MergeDecision(field, _ref(chosen), "longest", others))
    return value


def merge_records(group: Sequence[PaperRecord]) -> MergeResult:
    """Merge one duplicate group (anchor first) into a single record plus the log of what was done."""
    if not group:
        raise MergeError("There is nothing to merge")
    dois = {d for d in (doi_key(r.doi) for r in group) if d}
    if len(dois) > 1:
        raise MergeError("These records carry different DOIs and must not be merged: " + ", ".join(sorted(dois)))

    anchor = group[0]
    decisions: list[MergeDecision] = []

    # Source ids: every member's own id plus the ids it carried; anchor wins a conflict.
    source_ids: dict[str, str] = {}
    for record in group:
        for key, value in {**record.source_ids, record.connector: record.external_id}.items():
            if key not in source_ids:
                source_ids[key] = value
            elif source_ids[key] != value:
                decisions.append(
                    MergeDecision(f"source_ids.{key}", _ref(anchor), "id_conflict", (f"{_ref(record)} ({value})",))
                )

    doi = next((d for d in (doi_key(r.doi) for r in group) if d), None)
    abstract = _longest("abstract", group, decisions)
    authors = _longest("authors", group, decisions) or ()
    flags = list(dict.fromkeys(flag for r in group for flag in r.quality_flags))
    flags = [f for f in flags if f != "no_abstract" and not (f == "invalid_doi" and doi)]
    if not abstract:
        flags.append("no_abstract")

    merged = PaperRecord(
        connector=anchor.connector,
        external_id=anchor.external_id,
        title=_first_present("title", group, decisions),
        doi=doi,
        authors=tuple(authors),
        year=_first_present("year", group, decisions),
        venue=_first_present("venue", group, decisions),
        abstract=abstract,
        url=_first_present("url", group, decisions),
        oa_url=_first_present("oa_url", group, decisions),
        source_ids=source_ids,
        quality_flags=tuple(flags),
    )
    matched_by = {_ref(anchor): "anchor"}
    for record in group[1:]:
        matched_by[_ref(record)] = match_kind(anchor, record) or "group"
    return MergeResult(merged, tuple(_ref(r) for r in group), matched_by, tuple(decisions))
