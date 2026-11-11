"""Bias and coverage warnings for a project's literature (spec 7.8, plan X.6).

Three checks, all counted from stored data (rule 24), each a prompt to look and never a verdict:

* `database_coverage`: how many databases the project has actually searched. One database is a coverage risk;
  searches that were saved but never run do not count. Manual adds and snowballing still widen coverage, so
  the message says so.
* `citation_count_bias`: when the brief sets a citation threshold, how many sources it hides, how many of
  those are recent (their counts have had little time to grow), and how many sources have no count at all.
  A missing count is unknown, never 0 (`app/citation_rank.py`).
* `language_bias`: counts sources by the language a connector reported (X.86). Sources with none are counted as
  unknown, never as English; with no language recorded anywhere the check says it cannot be assessed.

Sources merged away or excluded at screening are left out, the same rule the clustering uses.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, ResearchBrief, SearchQuery
from app.thematic_clusters import clusterable_sources

# A source this recent has had little time to be cited, so a citation threshold works against it.
RECENT_YEARS = 2


def _database_coverage(db: Session, project: Project) -> dict[str, Any]:
    names = sorted(
        {
            database
            for database in db.scalars(
                select(SearchQuery.database).where(SearchQuery.project_id == project.id, SearchQuery.run_at.is_not(None))
            )
        }
    )
    if not names:
        return {
            "code": "database_coverage", "level": "info", "databases": [],
            "message": "No search has been run yet, so database coverage cannot be judged.",
        }
    if len(names) == 1:
        return {
            "code": "database_coverage", "level": "warning", "databases": names,
            "message": f"Only one database has been searched ({names[0]}). No single database indexes everything; "
            "search a second one, or add known papers by hand, before relying on this set.",
        }
    return {}


def _citation_count_bias(db: Session, project: Project) -> dict[str, Any]:
    threshold = db.scalar(select(ResearchBrief.min_citations).where(ResearchBrief.project_id == project.id))
    if threshold is None:
        return {}
    recent_from = datetime.now(timezone.utc).year - RECENT_YEARS + 1
    sources = clusterable_sources(db, project)
    below = [s for s in sources if s.citation_count is not None and s.citation_count < threshold]
    unknown = sum(1 for s in sources if s.citation_count is None)
    if not below:
        return {}
    recent = sum(1 for s in below if s.year is not None and s.year >= recent_from)
    return {
        "code": "citation_count_bias", "level": "warning", "threshold": threshold,
        "hidden_below_threshold": len(below), "hidden_recent": recent, "unknown_count": unknown,
        "message": f"The citation threshold of {threshold} hides {len(below)} source(s), {recent} of them from the last "
        f"{RECENT_YEARS} years, whose counts have had little time to grow. Highly cited work gets cited more, so a "
        f"threshold favours already-established papers. {unknown} source(s) have no count and are not hidden.",
    }


def _language_bias(db: Session, project: Project) -> dict[str, Any]:
    sources = clusterable_sources(db, project)
    by_language = dict(sorted(Counter(s.language for s in sources if s.language).items()))
    unknown = sum(1 for s in sources if not s.language)
    if not by_language:
        return {
            "code": "language_bias", "level": "info", "assessable": False,
            "message": "Language bias cannot be assessed yet: no source records its language. Most scholarly databases "
            "over-represent English, so treat this set as English-weighted unless you searched otherwise.",
        }
    base = {"code": "language_bias", "assessable": True, "by_language": by_language, "unknown_language": unknown}
    note = f" {unknown} source(s) have no recorded language." if unknown else ""
    if len(by_language) == 1:
        (only,) = by_language
        return {**base, "level": "warning",
                "message": f"Every source with a recorded language is in one language ({only}). Work in other languages "
                f"is missing from this set unless it was left out on purpose.{note}"}
    return {**base, "level": "info",
            "message": "Sources span " + ", ".join(f"{lang} ({n})" for lang, n in by_language.items()) + f".{note}"}


def bias_warnings(db: Session, project: Project) -> list[dict[str, Any]]:
    checks = (_database_coverage(db, project), _citation_count_bias(db, project), _language_bias(db, project))
    return [check for check in checks if check]
