"""Turn a `BooleanQuery` (M1.5.1) into the query string each database understands (spec 5.2.1).

Each adapter returns an `AdaptedQuery`:

* `text`   - what to send as the search string,
* `exact`  - True when the database will apply the blocks' AND/OR logic as written,
* `caveats`- plain-language reasons it might not (shown to the scholar and kept in the search
             log so the search can be reproduced and reported honestly).

When a database cannot express Boolean logic, the adapter does **not** pretend: it returns the
closest plain-text query with `exact=False` and a caveat saying results are ranked, not
filtered, by the blocks. A caller must keep the caveats with the search record.

Syntax per database (what each adapter assumes; none has been checked against the live service
yet, which is why wildcard use is flagged for the services where it is not clearly documented):

* openalex         `search=` accepts uppercase AND / OR with quotes and parentheses.
* arxiv            `search_query=` accepts `all:"phrase"` terms with AND / OR and parentheses.
* pubmed           `"phrase"[tiab]` field-tagged terms (title/abstract) with AND / OR; the tag
                   stops PubMed's automatic term mapping from widening the search.
* semantic_scholar `/paper/search` is plain-text relevance search with no operators.
* crossref         `query=` is free text relevance search with no operators.

Filters (year, document type, ...) are not part of the string; they travel in
`SearchRequest.filters`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.search_query import MAX_QUERY_CHARS, BooleanQuery, ConceptBlock, QueryError, is_wildcard

WILDCARD_UNVERIFIED = "Wildcard (*) handling is not verified for this database; check that truncated terms matched."


@dataclass(frozen=True)
class AdaptedQuery:
    database: str
    text: str
    exact: bool
    caveats: tuple[str, ...] = ()


def _has_wildcard(query: BooleanQuery) -> bool:
    return any(is_wildcard(t) for b in query.blocks for t in b.terms)


def _quoted(term: str) -> str:
    return term if is_wildcard(term) else f'"{term}"'


def _join(query: BooleanQuery, term: Callable[[str], str]) -> str:
    return " AND ".join("(" + " OR ".join(term(t) for t in b.terms) + ")" for b in query.blocks)


def _flat(blocks: tuple[ConceptBlock, ...]) -> str:
    seen: set[str] = set()
    words = []
    for block in blocks:
        for term in block.terms:
            if term.casefold() not in seen:
                seen.add(term.casefold())
                words.append(term.rstrip("*"))
    return " ".join(words)


def openalex(query: BooleanQuery) -> AdaptedQuery:
    caveats = (WILDCARD_UNVERIFIED,) if _has_wildcard(query) else ()
    return AdaptedQuery("openalex", _join(query, _quoted), exact=not caveats, caveats=caveats)


def arxiv(query: BooleanQuery) -> AdaptedQuery:
    caveats = (WILDCARD_UNVERIFIED,) if _has_wildcard(query) else ()
    return AdaptedQuery("arxiv", _join(query, lambda t: "all:" + _quoted(t)), exact=not caveats, caveats=caveats)


def pubmed(query: BooleanQuery) -> AdaptedQuery:
    # PubMed truncation (`employ*[tiab]`) works only on unquoted words, which is how wildcards render.
    return AdaptedQuery("pubmed", _join(query, lambda t: _quoted(t) + "[tiab]"), exact=True)


def semantic_scholar(query: BooleanQuery) -> AdaptedQuery:
    caveat = (
        "Semantic Scholar relevance search has no Boolean operators: the terms are searched together "
        "and ranked by similarity, not filtered by your concept blocks. Screen results accordingly."
    )
    caveats = (caveat,) + ((WILDCARD_UNVERIFIED.replace("this database", "Semantic Scholar"),) if _has_wildcard(query) else ())
    return AdaptedQuery("semantic_scholar", _flat(query.blocks), exact=False, caveats=caveats)


def crossref(query: BooleanQuery) -> AdaptedQuery:
    caveat = (
        "Crossref search has no Boolean operators: the terms are searched together as free text and "
        "ranked by relevance, not filtered by your concept blocks. Screen results accordingly."
    )
    return AdaptedQuery("crossref", _flat(query.blocks), exact=False, caveats=(caveat,))


ADAPTERS: dict[str, Callable[[BooleanQuery], AdaptedQuery]] = {
    "openalex": openalex,
    "semantic_scholar": semantic_scholar,
    "crossref": crossref,
    "arxiv": arxiv,
    "pubmed": pubmed,
}


def adapt(query: BooleanQuery, database: str) -> AdaptedQuery:
    """The query for `database`. Unknown databases raise `QueryError`; an over-long result too."""
    adapter = ADAPTERS.get(database)
    if adapter is None:
        raise QueryError(f"No query syntax for {database!r}. Supported: {', '.join(sorted(ADAPTERS))}")
    adapted = adapter(query)
    if not adapted.text.strip():
        raise QueryError(f"The query has nothing to search for in {database}")
    if len(adapted.text) > MAX_QUERY_CHARS:
        raise QueryError(
            f"The {database} query is {len(adapted.text)} characters; the limit is {MAX_QUERY_CHARS}. Remove some terms."
        )
    return adapted
