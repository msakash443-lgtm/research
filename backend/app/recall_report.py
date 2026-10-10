"""Gold-set recall report: what share of the known-relevant papers did the searches find, and why
were the others missed? (spec 5.2 quality checks and 11, target >= 95%; M1.8.3)

The gold set is a list of papers a scholar knows are relevant. In a project these are its seed
papers (M1.8.2); the X.15 evaluation fixture can be passed in the same shape (`GoldPaper`).
Matching is the known-item rule (`known_items._matches`): same DOI, else same normalised title with
year / first author checked only when the gold record gives them.

For every missed paper the report says why, using only the stored search runs (no network calls):

* `doi_differs`     - a result has the same title but another DOI (often preprint vs published);
* `metadata_differs` - a result has the same title but the year or first author disagrees;
* `result_cap`      - at least one search stopped at its result cap, so the paper may lie beyond it;
* `not_retrieved`   - nothing with this DOI or title came back: the query may not cover it, or the
                      databases searched may not index it (the report can't tell which).

Papers with no DOI and a title too short to identify are listed as `unchecked` and left out of the
recall figure rather than guessed. The report informs the people reviewing the search; it never
blocks or approves anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dedupe import doi_key, first_author_key, normalise_title
from app.known_items import _checkable, _matches
from app.models import Project, SeedPaper
from app.prisma import _latest_runs

DEFAULT_TARGET = 0.95


@dataclass(frozen=True)
class GoldPaper:
    id: str
    title: str
    doi: str | None = None
    year: int | None = None
    authors: tuple[str, ...] = field(default_factory=tuple)


class RunLike(Protocol):
    search_id: Any
    database: str
    version: int
    counts: dict[str, Any]
    results: list[dict[str, Any]] | None


def _split_key(entry: dict[str, Any]) -> tuple[str, str, str] | None:
    key = entry.get("work_key")
    if not key:
        return None
    title, year, author = (key.split("|") + ["", ""])[:3]
    return title, year, author


def _reasons(paper: GoldPaper, runs: list[RunLike], capped: bool) -> list[dict[str, str]]:
    title = normalise_title(paper.title)
    paper_doi = doi_key(paper.doi)
    other_dois: set[str] = set()
    differing: list[str] = []
    for run in runs:
        for entry in run.results or []:
            parts = _split_key(entry)
            if not parts or parts[0] != title:
                continue
            entry_doi = entry.get("doi")
            if paper_doi and entry_doi and entry_doi != paper_doi:
                other_dois.add(entry_doi)
                continue
            _, year, author = parts
            what = []
            if paper.year is not None and str(paper.year) != year:
                what.append(f"year {year or 'unknown'}")
            paper_author = first_author_key(paper.authors)
            if paper_author and paper_author != author:
                what.append(f"first author '{author or 'unknown'}'")
            if what:
                differing.append(f"{run.database}: {', '.join(what)}")
    reasons = []
    if other_dois:
        reasons.append(
            {
                "code": "doi_differs",
                "detail": "A result with the same title has a different DOI ("
                + ", ".join(sorted(other_dois))
                + "); it may be another version of the paper (e.g. preprint vs published)",
            }
        )
    if differing:
        reasons.append(
            {
                "code": "metadata_differs",
                "detail": "A result with the same title was retrieved but its metadata disagrees ("
                + "; ".join(sorted(set(differing)))
                + "); check the gold record or the database record",
            }
        )
    if capped:
        reasons.append(
            {"code": "result_cap", "detail": "At least one search stopped at its result cap, so the paper may lie beyond it"}
        )
    if not other_dois and not differing:
        reasons.append(
            {
                "code": "not_retrieved",
                "detail": "No search returned a record with this DOI or title: the search terms may not cover it, "
                "or the databases searched may not index it",
            }
        )
    return reasons


def report(gold: Iterable[GoldPaper], runs: list[RunLike], target: float = DEFAULT_TARGET) -> dict[str, Any]:
    """Recall of *gold* against *runs* (use only the latest version of each search)."""
    gold = list(gold)
    searches = [
        {
            "search_id": str(r.search_id),
            "database": r.database,
            "version": r.version,
            "truncated": bool((r.counts or {}).get("truncated")),
        }
        for r in runs
    ]
    capped = any(s["truncated"] for s in searches)
    unchecked, missed, found = [], [], []
    for paper in gold:
        if not _checkable(paper):
            unchecked.append(
                {
                    "id": paper.id,
                    "title": paper.title,
                    "reason": "No DOI and the title is too short to identify the paper reliably",
                }
            )
            continue
        found_in = [str(r.search_id) for r in runs if any(_matches(paper, e) for e in (r.results or []))]
        if found_in:
            found.append({"id": paper.id, "title": paper.title, "found_in": found_in})
        elif runs:
            missed.append({"id": paper.id, "title": paper.title, "doi": paper.doi, "reasons": _reasons(paper, runs, capped)})

    checked = len(found) + len(missed)
    exact_recall = (len(found) / checked) if runs and checked else None
    if not gold:
        state = "no_gold_set"
    elif not runs:
        state = "not_searched"
    elif exact_recall is None:
        state = "nothing_checkable"
    else:
        state = "meets_target" if exact_recall >= target else "below_target"
    return {
        "target": target,
        "status": state,
        "meets_target": None if exact_recall is None else exact_recall >= target,
        "recall": round(exact_recall, 3) if exact_recall is not None else None,
        "gold_papers": len(gold),
        "checked": checked if runs else 0,
        "found": len(found),
        "missed": len(missed),
        "missed_papers": missed,
        "found_papers": found,
        "unchecked": unchecked,
        "searches": searches,
    }


def project_report(db: Session, project: Project, target: float = DEFAULT_TARGET) -> dict[str, Any]:
    """The report for a project, with its seed papers as the gold set."""
    seeds = db.scalars(
        select(SeedPaper).where(SeedPaper.project_id == project.id).order_by(SeedPaper.created_at, SeedPaper.id)
    ).all()
    gold = [
        GoldPaper(id=str(s.id), title=s.title, doi=s.doi, year=s.year, authors=tuple(s.authors or ()))
        for s in seeds
    ]
    return report(gold, _latest_runs(db, project), target)
