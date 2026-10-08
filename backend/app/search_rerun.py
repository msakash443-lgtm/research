"""Can a stored search be re-run like-for-like? (spec 5.2.5 reproducibility, plan M1.5.6)

Kept apart from the search runner so the API can refuse a re-run up front without importing the
code that runs searches (only the G2-gated task handler may do that).
"""

from __future__ import annotations

from app.models import SearchQuery

# Semantic Scholar searches saved before M1.5.5 hold a flat word list made for its relevance search,
# marked by this caveat. The bulk endpoint now used reads such a string as "all these words", so a
# re-run would not repeat the original search (M1.5.6).
LEGACY_S2_CAVEAT = "Semantic Scholar relevance search has no Boolean operators"
LEGACY_S2_RERUN = (
    "This Semantic Scholar search was saved before searches switched to Semantic Scholar's Boolean (bulk) "
    "search. Its stored query was a plain word list for relevance ranking, which the Boolean search would "
    "read as 'all of these words', so a re-run would not repeat the original search and its counts would "
    "not be comparable. Start a new search from your concept blocks instead."
)


def rerun_refusal(row: SearchQuery) -> str | None:
    """Why this stored search can't be re-run like-for-like, or None."""
    if row.database == "semantic_scholar" and any(LEGACY_S2_CAVEAT in str(c) for c in row.caveats or []):
        return LEGACY_S2_RERUN
    return None
