"""Known-item recall check: do the project's seed papers appear in the search results? (spec 5.2, M1.8.2)

For each seed paper and each search (latest version of each), the stored result list is searched:

* same DOI -> found;  two different DOIs -> never the same paper;
* otherwise the seed's title must equal the result's normalised title, and the year / first author
  must agree **when the seed gives them** (a seed without a year still matches).
* A seed with no DOI and too short a title to identify (see `dedupe.work_key`) is reported as
  `cannot_check`, not as missing: guessing either way would mislead.

A seed counts as found overall if any search found it. A missing seed is **flagged**, and the note
says when a search that hit its result cap could be the reason. The check informs the human who
reviews the sample at gate G2; it never blocks or approves anything.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dedupe import DedupeRecord, doi_key, first_author_key, normalise_title, work_key
from app.models import Project, SeedPaper
from app.prisma import _latest_runs


def _matches(seed: SeedPaper, entry: dict[str, Any]) -> bool:
    seed_doi, doi = doi_key(seed.doi), entry.get("doi")
    if seed_doi and doi:
        return seed_doi == doi
    key = entry.get("work_key")
    if not key:
        return False
    title, year, author = (key.split("|") + ["", ""])[:3]
    if normalise_title(seed.title) != title:
        return False
    if seed.year is not None and str(seed.year) != year:
        return False
    seed_author = first_author_key(seed.authors)
    return not seed_author or seed_author == author


def _checkable(seed: SeedPaper) -> bool:
    # Year and author don't change a title's weight, so a title-only record tells us if it can identify a work.
    return bool(doi_key(seed.doi)) or work_key(DedupeRecord(title=seed.title)) is not None


def check(db: Session, project: Project) -> dict[str, Any]:
    seeds = db.scalars(
        select(SeedPaper).where(SeedPaper.project_id == project.id).order_by(SeedPaper.created_at, SeedPaper.id)
    ).all()
    runs = _latest_runs(db, project)
    run_info = [
        {"search_id": str(r.search_id), "database": r.database, "version": r.version, "truncated": bool(r.counts.get("truncated"))}
        for r in runs
    ]
    capped = any(r["truncated"] for r in run_info)
    items = []
    for seed in seeds:
        if not _checkable(seed):
            items.append(
                {
                    "seed_id": str(seed.id),
                    "title": seed.title,
                    "status": "cannot_check",
                    "reason": "No DOI and the title is too short to identify the paper reliably",
                }
            )
            continue
        found_in = [str(r.search_id) for r in runs if any(_matches(seed, e) for e in (r.results or []))]
        status = "found" if found_in else ("missing" if runs else "not_searched")
        item = {"seed_id": str(seed.id), "title": seed.title, "status": status, "found_in": found_in}
        if status == "missing" and capped:
            item["note"] = "At least one search stopped at its result cap, so this may be a cap effect"
        items.append(item)
    checked = [i for i in items if i["status"] in ("found", "missing")]
    missing = [i for i in items if i["status"] == "missing"]
    flags = []
    if missing:
        flags.append(f"{len(missing)} of {len(checked)} seed papers were not found by the searches: review the search strategy")
    if any(i["status"] == "cannot_check" for i in items):
        flags.append("Some seed papers cannot be checked: add a DOI or a fuller title")
    return {
        "seeds": len(items),
        "checked": len(checked),
        "found": len(checked) - len(missing),
        "missing": len(missing),
        "flagged": bool(missing),
        "recall_of_seeds": round((len(checked) - len(missing)) / len(checked), 3) if checked and runs else None,
        "flags": flags,
        "searches": run_info,
        "items": items,
    }
