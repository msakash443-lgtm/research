import pytest

from app.connectors.base import SearchRequest
from app.search_query import MAX_QUERY_CHARS, BooleanQuery, ConceptBlock, QueryError, render
from app.search_query_adapters import ADAPTERS, adapt


def q(*blocks):
    return BooleanQuery(blocks=tuple(ConceptBlock(terms=b) for b in blocks))


BASIC = q(("female labour", "women's work"), ("India", "South Asia"))
WILD = q(("employ*", "job search"), ("India",))


def test_every_supported_database_has_an_adapter_and_the_set_is_pinned():
    assert set(ADAPTERS) == {
        "openalex", "semantic_scholar", "crossref", "arxiv", "pubmed", "core",
        "scopus", "web_of_science", "ieee_xplore",
    }


def test_core_keeps_the_boolean_string_but_says_it_searches_full_text():
    a = adapt(BASIC, "core")
    assert a.text == render(BASIC)
    assert a.exact is False and "full text" in a.caveats[0]
    assert any("Wildcard" in c for c in adapt(WILD, "core").caveats)


def test_openalex_keeps_the_generic_boolean_string():
    a = adapt(BASIC, "openalex")
    assert a.text == '("female labour" OR "women\'s work") AND ("India" OR "South Asia")' == render(BASIC)
    assert a.exact is True and a.caveats == ()


def test_arxiv_field_prefixes_every_term():
    a = adapt(BASIC, "arxiv")
    assert a.text == '(all:"female labour" OR all:"women\'s work") AND (all:"India" OR all:"South Asia")'
    assert a.exact is True


def test_pubmed_tags_every_term_with_title_abstract():
    a = adapt(BASIC, "pubmed")
    assert a.text == '("female labour"[tiab] OR "women\'s work"[tiab]) AND ("India"[tiab] OR "South Asia"[tiab])'
    assert a.exact is True and a.caveats == ()


def test_pubmed_wildcards_stay_unquoted_before_the_tag():
    assert adapt(WILD, "pubmed").text == '(employ*[tiab] OR "job search"[tiab]) AND ("India"[tiab])'


@pytest.mark.parametrize("database", ["crossref"])
def test_databases_without_boolean_logic_are_never_reported_as_exact(database):
    a = adapt(BASIC, database)
    assert a.exact is False
    assert a.text == "female labour women's work India South Asia"  # flat, no operators, no quotes
    assert "AND" not in a.text and '"' not in a.text
    assert any("not filtered by your concept blocks" in c for c in a.caveats)


def test_flat_text_drops_duplicates_across_blocks_and_wildcard_stars():
    flat = adapt(q(("Women", "employ*"), ("women", "pay")), "crossref").text
    assert flat == "Women employ pay"


@pytest.mark.parametrize("database", ["openalex", "arxiv", "semantic_scholar"])
def test_wildcards_where_support_is_unverified_are_flagged_inexact(database):
    a = adapt(WILD, database)
    assert a.exact is False and any("Wildcard" in c for c in a.caveats)
    assert adapt(BASIC, database).exact is True  # only flagged when a wildcard is actually used


def test_semantic_scholar_uses_bulk_search_syntax_and_is_exact():
    a = adapt(BASIC, "semantic_scholar")
    assert a.text == '("female labour" | "women\'s work") + ("India" | "South Asia")'
    assert a.exact is True and a.caveats == ()
    assert adapt(WILD, "semantic_scholar").text == '(employ* | "job search") + ("India")'


def test_licensed_connectors_use_their_boolean_query_syntax():
    scopus = adapt(BASIC, "scopus")
    assert scopus.text == 'TITLE-ABS-KEY(("female labour" OR "women\'s work") AND ("India" OR "South Asia"))'
    assert scopus.exact is True

    wos = adapt(BASIC, "web_of_science")
    assert wos.text == 'TS=(("female labour" OR "women\'s work") AND ("India" OR "South Asia"))'
    assert wos.exact is True

    ieee = adapt(BASIC, "ieee_xplore")
    assert ieee.text == render(BASIC)
    assert ieee.exact is True


def test_unknown_database_is_refused_listing_the_supported_ones():
    with pytest.raises(QueryError, match="openalex"):
        adapt(BASIC, "no_such_database")


def test_adapted_text_is_always_accepted_by_a_connector_request():
    big = BooleanQuery(blocks=tuple(ConceptBlock(terms=tuple(f"term {b} {i}" for i in range(20))) for b in range(5)))
    assert len(render(big)) < MAX_QUERY_CHARS
    for database in ADAPTERS:
        try:
            text = adapt(big, database).text
        except QueryError:
            continue  # refused loudly is acceptable; silently over-long is not
        SearchRequest(query=text)


def test_a_tag_that_pushes_the_query_over_the_limit_is_refused_not_truncated():
    near = BooleanQuery(blocks=tuple(ConceptBlock(terms=tuple(f"{'w' * 38}{b}{i:02d}" for i in range(10))) for b in range(4)))
    assert len(render(near)) <= MAX_QUERY_CHARS  # fine as the generic string
    assert len(adapt(near, "openalex").text) <= MAX_QUERY_CHARS
    with pytest.raises(QueryError, match="characters"):
        adapt(near, "pubmed")  # the [tiab] tags make it too long


def test_hostile_terms_cannot_reach_any_adapter():
    # ConceptBlock refuses them first, so no adapter ever sees quotes, parentheses or operators.
    for bad in ('x" OR "y', "a) AND (b", "NOT"):
        with pytest.raises(Exception):
            q((bad,))


def test_adapters_do_not_change_the_block_structure():
    for database in ("openalex", "arxiv", "pubmed", "scopus", "web_of_science", "ieee_xplore"):
        text = adapt(BASIC, database).text
        assert text.count(" AND ") == 1
        assert text.count("(") == text.count(")") and text.count("(") >= 2
