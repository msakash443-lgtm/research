"""X.180/X.181: one connector contract suite.

Every connector test file used to carry its own copy of a handful of generic
assertions (protocol conformance, key requirement, `max_related` validation,
work normalisation, flags/degraded fields, get-by-id/missing record, search
request shape, result-window cap, bad-filter validation). This file is the
single home for those shared behaviours, parametrized per connector; each
per-connector test file keeps only the assertions that are genuinely specific
to its own payload shape.

Deleted test -> replacing contract case (test name below, connector id as
pytest id):
  test_core_connector.py::test_it_satisfies_the_connector_protocol        -> test_it_satisfies_the_connector_protocol[core]
  test_core_connector.py::test_work_normalisation                        -> test_work_normalisation[core]
  test_crossref_connector.py::test_it_satisfies_the_connector_protocol   -> test_it_satisfies_the_connector_protocol[crossref]
  test_crossref_connector.py::test_work_normalisation                   -> test_work_normalisation[crossref]
  test_crossref_connector.py::test_search_sends_query_filters_paging_and_contact -> test_search_sends_query_filters_paging_and_contact[crossref]
  test_crossref_connector.py::test_bad_filters_fail_before_any_request  -> test_bad_filters_fail_before_any_request[crossref-*] (X.181)
  test_crossref_connector.py::test_invalid_max_related                 -> test_invalid_max_related[crossref]
  test_openalex_connector.py::test_it_satisfies_the_connector_protocol  -> test_it_satisfies_the_connector_protocol[openalex]
  test_openalex_connector.py::test_work_normalisation                  -> test_work_normalisation[openalex]
  test_openalex_connector.py::test_flags_and_degraded_fields           -> test_flags_and_degraded_fields[openalex]
  test_openalex_connector.py::test_search_sends_query_filters_paging_and_contact -> test_search_sends_query_filters_paging_and_contact[openalex]
  test_openalex_connector.py::test_bad_or_unknown_filters_fail_before_any_request -> test_bad_filters_fail_before_any_request[openalex-*] (X.181)
  test_openalex_connector.py::test_get_by_id_and_missing_record        -> test_get_by_id_and_missing_record[openalex]
  test_openalex_connector.py::test_invalid_max_related                -> test_invalid_max_related[openalex]
  test_opencitations_connector.py::test_it_satisfies_the_connector_protocol -> test_it_satisfies_the_connector_protocol[opencitations]
  test_pubmed_connector.py::test_it_satisfies_the_connector_protocol   -> test_it_satisfies_the_connector_protocol[pubmed]
  test_pubmed_connector.py::test_get_by_id_and_missing_record          -> test_get_by_id_and_missing_record[pubmed]
  test_semantic_scholar_connector.py::test_it_satisfies_the_connector_protocol -> test_it_satisfies_the_connector_protocol[semantic_scholar]
  test_semantic_scholar_connector.py::test_flags_and_degraded_fields   -> test_flags_and_degraded_fields[semantic_scholar]
  test_semantic_scholar_connector.py::test_invalid_max_related         -> test_invalid_max_related[semantic_scholar]
  test_unpaywall_connector.py::test_it_satisfies_the_connector_protocol -> test_it_satisfies_the_connector_protocol[unpaywall]
  test_ieee_xplore_connector.py::test_it_satisfies_connector_protocol_and_requires_a_key -> test_it_satisfies_the_connector_protocol[ieee_xplore] + test_a_missing_api_key_is_refused[ieee_xplore]
  test_ieee_xplore_connector.py::test_last_page_reports_the_api_result_window_cap -> test_last_page_reports_the_api_result_window_cap[ieee_xplore]
  test_scopus_connector.py::test_it_satisfies_connector_protocol_and_requires_a_key (isinstance + no-key parts) -> test_it_satisfies_the_connector_protocol[scopus] + test_a_missing_api_key_is_refused[scopus]
  test_scopus_connector.py::test_last_page_reports_the_api_result_window_cap -> test_last_page_reports_the_api_result_window_cap[scopus]
  test_web_of_science_connector.py::test_it_satisfies_connector_protocol_and_requires_a_key (isinstance + no-key parts) -> test_it_satisfies_the_connector_protocol[web_of_science] + test_a_missing_api_key_is_refused[web_of_science]
  test_web_of_science_connector.py::test_last_page_reports_the_conservative_result_window_cap -> test_last_page_reports_the_api_result_window_cap[web_of_science]

Everything connector-specific (header names, payload shapes, filter sets,
cursor encodings, fulltext handling, related-record paging, ...) stays in the
per-connector files, including the blank-key and header checks for the three
keyed connectors.
"""

import httpx
import pytest
from pydantic import SecretStr

from app.connectors import Connector, ConnectorError, SearchRequest
from app.connectors.core import CoreConnector, to_record as core_to_record
from app.connectors.crossref import CrossrefConnector, to_record as crossref_to_record
from app.connectors.ieee_xplore import MAX_OFFSET as IEEE_MAX_OFFSET
from app.connectors.ieee_xplore import IeeeXploreConnector
from app.connectors.openalex import OpenAlexConnector, to_record as openalex_to_record
from app.connectors.scopus import MAX_OFFSET as SCOPUS_MAX_OFFSET
from app.connectors.scopus import ScopusConnector
from app.connectors.semantic_scholar import SemanticScholarConnector, to_record as semantic_scholar_to_record
from app.connectors.web_of_science import MAX_PAGES as WOS_MAX_PAGES
from app.connectors.web_of_science import WebOfScienceConnector

from tests.test_core_connector import make as core_make
from tests.test_core_connector import work as core_work
from tests.test_crossref_connector import connector as crossref_connector
from tests.test_crossref_connector import item as crossref_item
from tests.test_crossref_connector import ok as crossref_ok
from tests.test_ieee_xplore_connector import article as ieee_article
from tests.test_ieee_xplore_connector import make as ieee_make
from tests.test_openalex_connector import connector as openalex_connector
from tests.test_openalex_connector import work as openalex_work
from tests.test_opencitations_connector import make as opencitations_make
from tests.test_pubmed_connector import article as pubmed_article
from tests.test_pubmed_connector import feed as pubmed_feed
from tests.test_pubmed_connector import make as pubmed_make
from tests.test_scopus_connector import entry as scopus_entry
from tests.test_scopus_connector import make as scopus_make
from tests.test_semantic_scholar_connector import connector as semantic_scholar_connector
from tests.test_semantic_scholar_connector import paper as semantic_scholar_paper
from tests.test_unpaywall_connector import connector as unpaywall_connector
from tests.test_web_of_science_connector import hit as wos_hit
from tests.test_web_of_science_connector import make as wos_make

# --- protocol conformance (every connector) ---------------------------------------

PROTOCOL_CASES = [
    pytest.param(lambda: core_make(lambda r: {})[0], id="core"),
    pytest.param(lambda: crossref_connector(lambda r: {})[0], id="crossref"),
    pytest.param(lambda: openalex_connector(lambda r: {})[0], id="openalex"),
    pytest.param(lambda: opencitations_make(lambda r: [])[0], id="opencitations"),
    pytest.param(lambda: pubmed_make(lambda r: {})[0], id="pubmed"),
    pytest.param(lambda: semantic_scholar_connector(lambda r: {})[0], id="semantic_scholar"),
    pytest.param(lambda: unpaywall_connector(lambda r: {})[0], id="unpaywall"),
    pytest.param(lambda: ieee_make(lambda request: {})[0], id="ieee_xplore"),
    pytest.param(lambda: scopus_make(lambda request: {})[0], id="scopus"),
    pytest.param(lambda: wos_make(lambda request: {})[0], id="web_of_science"),
]


@pytest.mark.parametrize("build", PROTOCOL_CASES)
def test_it_satisfies_the_connector_protocol(build):
    assert isinstance(build(), Connector)


# --- a missing API key is refused (keyed connectors) ------------------------------

KEY_REQUIRED_CASES = [
    pytest.param(IeeeXploreConnector, "IEEE_XPLORE_API_KEY", id="ieee_xplore"),
    pytest.param(ScopusConnector, "SCOPUS_API_KEY", id="scopus"),
    pytest.param(WebOfScienceConnector, "WEB_OF_SCIENCE_API_KEY", id="web_of_science"),
]


@pytest.mark.parametrize("connector_cls, env_var", KEY_REQUIRED_CASES)
def test_a_missing_api_key_is_refused(connector_cls, env_var):
    with pytest.raises(ConnectorError, match=env_var):
        connector_cls()


# --- invalid max_related (connectors that support related-record paging) ----------

MAX_RELATED_CASES = [
    pytest.param(CrossrefConnector, id="crossref"),
    pytest.param(OpenAlexConnector, id="openalex"),
    pytest.param(SemanticScholarConnector, id="semantic_scholar"),
]


@pytest.mark.parametrize("connector_cls", MAX_RELATED_CASES)
def test_invalid_max_related(connector_cls):
    with pytest.raises(ValueError):
        connector_cls(max_related=0)


# --- work normalisation (connectors sharing the plain "normalise a sample" shape) -

NORMALISATION_CASES = [
    pytest.param(
        lambda: core_to_record(core_work(7)),
        {
            "connector": "core",
            "external_id": "7",
            "title": "Open paper 7",
            "doi": "10.1000/c7",
            "authors": ("Ada Lovelace",),
            "year": 2019,
            "venue": "Journal of Open Things",
            "url": "https://core.ac.uk/works/1",
            "oa_url": "https://core.ac.uk/download/1.pdf",
            "source_ids": {"core": "7"},
        },
        id="core",
    ),
    pytest.param(
        lambda: crossref_to_record(crossref_item()),
        {
            "connector": "crossref",
            "external_id": "10.1000/p1",
            "doi": "10.1000/p1",
            "title": "Paper 1",
            "authors": ("Ada Lovelace", "ACME Consortium"),
            "year": 2020,
            "venue": "J. Testing",
            "abstract": "Hello & welcome",
            "oa_url": None,
            "source_ids": {"crossref": "10.1000/p1"},
        },
        id="crossref",
    ),
    pytest.param(
        lambda: openalex_to_record(openalex_work()),
        {
            "connector": "openalex",
            "external_id": "W1",
            "title": "Paper 1",
            "doi": "10.1000/p1",
            "authors": ("Ada Lovelace",),
            "year": 2020,
            "venue": "J. Testing",
            "abstract": "Hello world",
            "url": "https://pub.test/p",
            "oa_url": "https://oa.test/p.pdf",
            "source_ids": {"openalex": "W1", "pmid": "12345"},
        },
        id="openalex",
    ),
]


@pytest.mark.parametrize("build_record, expected", NORMALISATION_CASES)
def test_work_normalisation(build_record, expected):
    record = build_record()
    for field, value in expected.items():
        assert getattr(record, field) == value, field
    assert record.quality_flags == ()


# --- flags and degraded fields (connectors sharing a literal duplicate test name) -

DEGRADED_CASES = [
    pytest.param(
        lambda: openalex_to_record(
            openalex_work(is_retracted=True, type="preprint", abstract_inverted_index=None, doi="garbage", open_access={})
        ),
        {"retracted", "preprint", "no_abstract", "invalid_doi"},
        {"doi": None, "oa_url": None},
        id="openalex",
    ),
    pytest.param(
        lambda: semantic_scholar_to_record(
            semantic_scholar_paper(abstract=None, venue="arXiv.org", openAccessPdf=None, externalIds={"DOI": "bad"}, year="2020")
        ),
        {"preprint", "no_abstract", "invalid_doi"},
        {"doi": None, "oa_url": None, "year": None},
        id="semantic_scholar",
    ),
]


@pytest.mark.parametrize("build_record, expected_flags, expected_none", DEGRADED_CASES)
def test_flags_and_degraded_fields(build_record, expected_flags, expected_none):
    record = build_record()
    assert set(record.quality_flags) == expected_flags
    for field, value in expected_none.items():
        assert getattr(record, field) == value, field


# --- get-by-id for a known record, None for a missing one -------------------------


def _openalex_get_by_id():
    connector = openalex_connector(
        lambda r: openalex_work(7) if r.url.path == "/works/W7" else httpx.Response(404, json={})
    )[0]
    return connector, "https://openalex.org/W7", "W8", (lambda record: record.external_id == "W7")


def _pubmed_get_by_id():
    connector = pubmed_make(lambda r: pubmed_feed(pubmed_article(5)) if r.url.params["id"] == "5" else pubmed_feed())[0]
    return connector, "5", "6", (lambda record: record.title == "Remote work and wellbeing")


GET_BY_ID_CASES = [
    pytest.param(_openalex_get_by_id, id="openalex"),
    pytest.param(_pubmed_get_by_id, id="pubmed"),
]


@pytest.mark.parametrize("build", GET_BY_ID_CASES)
def test_get_by_id_and_missing_record(build):
    connector, known_id, missing_id, check = build()
    assert check(connector.get_by_id(known_id))
    assert connector.get_by_id(missing_id) is None


# --- search request shape: query, filters, paging and contact ---------------------


def _crossref_search_shape():
    c, server = crossref_connector(
        lambda r: crossref_ok({"total-results": 99, "next-cursor": "abc", "items": [crossref_item(1)]}),
        contact_email=" me@lab.example ",
    )
    page = c.search(SearchRequest(query="labour", limit=10, filters={"year_from": 2015, "year_to": 2020, "type": "journal-article"}))
    params = server.requests[0].url.params
    assert (params["query"], params["rows"], params["cursor"]) == ("labour", "10", "*")
    assert params["filter"] == "from-pub-date:2015,until-pub-date:2020,type:journal-article"
    assert params["mailto"] == "me@lab.example"
    assert (page.total, page.next_cursor, len(page.records)) == (99, "abc", 1)


def _openalex_search_shape():
    c, server = openalex_connector(
        lambda r: {"meta": {"count": 42, "next_cursor": "abc"}, "results": [openalex_work(1)]},
        contact_email=" me@lab.example ",
        api_key=SecretStr("test-openalex-key"),
    )
    page = c.search(
        SearchRequest(query="labour AND women", limit=10, filters={"year_from": 2015, "year_to": 2020, "type": "article", "is_oa": True})
    )
    params = server.requests[0].url.params
    assert params["search"] == "labour AND women" and params["per-page"] == "10" and params["cursor"] == "*"
    assert params["filter"] == "from_publication_date:2015-01-01,to_publication_date:2020-12-31,type:article,is_oa:true"
    assert params["mailto"] == "me@lab.example"
    assert params["api_key"] == "test-openalex-key"
    assert (page.total, page.next_cursor, len(page.records)) == (42, "abc", 1)

    c.search(SearchRequest(query="x", cursor="abc"))
    assert server.requests[1].url.params["cursor"] == "abc"
    assert "filter" not in server.requests[1].url.params


SEARCH_SHAPE_CASES = [
    pytest.param(_crossref_search_shape, id="crossref"),
    pytest.param(_openalex_search_shape, id="openalex"),
]


@pytest.mark.parametrize("check", SEARCH_SHAPE_CASES)
def test_search_sends_query_filters_paging_and_contact(check):
    check()


# --- bad filters fail before any request is sent (X.181: identical bodies) --------

BAD_FILTER_CASES = [
    pytest.param(lambda: crossref_connector(lambda r: {}), {"language": "en"}, id="crossref-language"),
    pytest.param(lambda: crossref_connector(lambda r: {}), {"year_to": "20x0"}, id="crossref-year_to"),
    pytest.param(lambda: crossref_connector(lambda r: {}), {"year_from": False}, id="crossref-year_from"),
    pytest.param(lambda: crossref_connector(lambda r: {}), {"type": "a,b"}, id="crossref-type-comma"),
    pytest.param(lambda: crossref_connector(lambda r: {}), {"type": "Journal Article"}, id="crossref-type-space"),
    pytest.param(lambda: openalex_connector(lambda r: {}), {"language": "en"}, id="openalex-language"),
    pytest.param(lambda: openalex_connector(lambda r: {}), {"year_from": "20x0"}, id="openalex-year_from-string"),
    pytest.param(lambda: openalex_connector(lambda r: {}), {"year_from": True}, id="openalex-year_from-bool"),
    pytest.param(lambda: openalex_connector(lambda r: {}), {"type": "a,b"}, id="openalex-type-comma"),
    pytest.param(lambda: openalex_connector(lambda r: {}), {"type": "Article; drop"}, id="openalex-type-semicolon"),
    pytest.param(lambda: openalex_connector(lambda r: {}), {"is_oa": "yes"}, id="openalex-is_oa"),
]


@pytest.mark.parametrize("build, filters", BAD_FILTER_CASES)
def test_bad_filters_fail_before_any_request(build, filters):
    c, server = build()
    with pytest.raises(ConnectorError):
        c.search(SearchRequest(query="x", filters=filters))
    assert server.requests == []


# --- last page reports the API's result-window cap --------------------------------


def _ieee_window_cap():
    connector, _ = ieee_make(lambda request: {"articles": [ieee_article()], "total_records": 50000})
    page = connector.search(SearchRequest(query="x", limit=1, cursor=str(IEEE_MAX_OFFSET - 1)))
    return page, connector


def _scopus_window_cap():
    connector, _ = scopus_make(
        lambda request: {"search-results": {"opensearch:totalResults": "10000", "entry": [scopus_entry()]}}
    )
    page = connector.search(SearchRequest(query="x", limit=1, cursor=str(SCOPUS_MAX_OFFSET - 1)))
    return page, connector


def _wos_window_cap():
    connector, _ = wos_make(lambda request: {"hits": [wos_hit()], "metadata": {"total": 10000}})
    page = connector.search(SearchRequest(query="x", limit=50, cursor=str(WOS_MAX_PAGES)))
    return page, connector


WINDOW_CAP_CASES = [
    pytest.param(_ieee_window_cap, id="ieee_xplore"),
    pytest.param(_scopus_window_cap, id="scopus"),
    pytest.param(_wos_window_cap, id="web_of_science"),
]


@pytest.mark.parametrize("build", WINDOW_CAP_CASES)
def test_last_page_reports_the_api_result_window_cap(build):
    page, connector = build()
    assert page.next_cursor is None and connector.last_search_capped is True
