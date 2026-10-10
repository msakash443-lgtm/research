"""Plan M1.4.7: IEEE Xplore Metadata Search API connector. Offline only."""

import httpx
import pytest
from pydantic import SecretStr

from app.connectors import Connector, ConnectorError, NotSupportedError, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.connectors.ieee_xplore import BASE_URL, MAX_OFFSET, IeeeXploreConnector, to_record


def article(ident="1234567", **overrides):
    value = {
        "article_number": ident,
        "title": "Research paper",
        "doi": "https://doi.org/10.1000/Test",
        "authors": {"authors": [{"full_name": "Ada Lovelace"}]},
        "publication_year": 2020,
        "publication_title": "Journal of Tests",
        "abstract": "An abstract.",
        "html_url": "https://ieeexplore.ieee.org/document/1234567",
    }
    value.update(overrides)
    return value


def make(handler):
    seen = []

    def inner(request):
        seen.append(request)
        response = handler(request)
        return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)

    http = ConnectorHttpClient(
        "ieee_xplore", BASE_URL, HttpPolicy(max_retries=0), transport=httpx.MockTransport(inner)
    )
    return IeeeXploreConnector(http=http, api_key=SecretStr("test-key")), seen


def test_it_satisfies_connector_protocol_and_requires_a_key():
    assert isinstance(make(lambda request: {})[0], Connector)
    with pytest.raises(ConnectorError, match="IEEE_XPLORE_API_KEY"):
        IeeeXploreConnector()


def test_normalizes_article_and_rejects_unusable_entries():
    record = to_record(article())
    assert record is not None
    assert record.external_id == "1234567" and record.doi == "10.1000/test"
    assert record.authors == ("Ada Lovelace",) and record.year == 2020
    assert record.venue == "Journal of Tests" and record.url == "https://ieeexplore.ieee.org/document/1234567"
    assert to_record(article(ident="nope")) is None
    degraded = to_record(article(doi="bad", abstract=None))
    assert degraded.quality_flags == ("invalid_doi", "no_abstract")


def test_search_uses_boolean_query_api_key_filters_and_offset_paging():
    connector, seen = make(lambda request: {"articles": [article(), article(ident="1234568")], "total_records": 8})
    page = connector.search(
        SearchRequest(query='("remote work") AND (India)', limit=2, cursor="2", filters={"year_from": 2018, "year_to": 2022})
    )
    assert len(page.records) == 2 and page.total == 8 and page.next_cursor == "4"
    params = seen[0].url.params
    assert params["apikey"] == "test-key" and params["format"] == "json"
    assert params["querytext"] == '("remote work") AND (India)'
    assert params["max_records"] == "2" and params["start_record"] == "3"
    assert params["start_year"] == "2018" and params["end_year"] == "2022"


def test_lookup_uses_article_number_parameter_without_an_empty_query():
    connector, seen = make(lambda request: {"articles": [article()], "total_records": 1})
    assert connector.get_by_id("1234567").title == "Research paper"
    assert seen[0].url.params["article_number"] == "1234567"
    assert "querytext" not in seen[0].url.params
    with pytest.raises(ConnectorError, match="numeric"):
        connector.get_by_id("10.1000/test")
    with pytest.raises(ConnectorError, match="Unsupported"):
        connector.search(SearchRequest(query="x", filters={"language": "English"}))
    with pytest.raises(ConnectorError, match="year_from"):
        connector.search(SearchRequest(query="x", filters={"year_from": 2022, "year_to": 2020}))
    with pytest.raises(ConnectorError, match="unexpected"):
        make(lambda request: {"unexpected": []})[0].search(SearchRequest(query="x"))


def test_offset_window_and_unsupported_capabilities_are_explicit():
    connector, _ = make(lambda request: {"articles": []})
    with pytest.raises(ConnectorError, match="10,000"):
        connector.search(SearchRequest(query="x", cursor=str(MAX_OFFSET)))
    with pytest.raises(NotSupportedError):
        connector.get_fulltext("1234567")


def test_last_page_reports_the_api_result_window_cap():
    connector, _ = make(lambda request: {"articles": [article()], "total_records": 50000})
    page = connector.search(SearchRequest(query="x", limit=1, cursor=str(MAX_OFFSET - 1)))
    assert page.next_cursor is None and connector.last_search_capped is True
