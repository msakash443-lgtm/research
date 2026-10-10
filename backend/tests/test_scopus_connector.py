"""Plan M1.4.7: Scopus connector. Offline only."""

import httpx
import pytest
from pydantic import SecretStr

from app.connectors import Connector, ConnectorError, NotSupportedError, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.connectors.scopus import BASE_URL, MAX_OFFSET, ScopusConnector, to_record


def entry(ident="SCOPUS_ID:2-s2.0-123", **overrides):
    value = {
        "dc:identifier": ident,
        "dc:title": "Research paper",
        "dc:creator": "Ada Lovelace",
        "prism:doi": "https://doi.org/10.1000/Test",
        "prism:coverDate": "2020-05-01",
        "prism:publicationName": "Journal of Tests",
        "dc:description": "An abstract.",
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
        "scopus", BASE_URL, HttpPolicy(max_retries=0), transport=httpx.MockTransport(inner)
    )
    return ScopusConnector(http=http), seen


def test_it_satisfies_connector_protocol_and_requires_a_key():
    assert isinstance(make(lambda request: {})[0], Connector)
    with pytest.raises(ConnectorError, match="SCOPUS_API_KEY"):
        ScopusConnector()
    with pytest.raises(ConnectorError, match="SCOPUS_API_KEY"):
        ScopusConnector(api_key=SecretStr(" "))
    connector = ScopusConnector(api_key=SecretStr("key"), inst_token=SecretStr("institution"))
    assert connector._http._client.headers["X-ELS-APIKey"] == "key"
    assert connector._http._client.headers["X-ELS-Insttoken"] == "institution"


def test_normalizes_search_result_and_skips_invalid_metadata():
    record = to_record(entry())
    assert record is not None
    assert (record.external_id, record.title, record.doi) == ("123", "Research paper", "10.1000/test")
    assert record.authors == ("Ada Lovelace",) and record.year == 2020
    assert record.venue == "Journal of Tests" and record.source_ids == {"scopus": "123"}
    assert to_record(entry("SCOPUS_ID:wrong")) is None
    degraded = to_record(entry(**{"prism:doi": "not-a-doi", "dc:description": None}))
    assert degraded.quality_flags == ("invalid_doi", "no_abstract")


def test_search_maps_paging_query_filters_and_skips_unusable_entries():
    connector, seen = make(
        lambda request: {
            "search-results": {
                "opensearch:totalResults": "5",
                "entry": [entry(), entry("SCOPUS_ID:invalid")],
            }
        }
    )
    page = connector.search(
        SearchRequest(
            query='TITLE-ABS-KEY("remote work")',
            limit=3,
            cursor="2",
            filters={"year_from": 2018, "year_to": 2022},
        )
    )
    assert [record.external_id for record in page.records] == ["123"]
    assert page.total == 5 and page.next_cursor == "4" and connector.skipped_records == 1
    params = seen[0].url.params
    assert params["query"] == '(TITLE-ABS-KEY("remote work")) AND PUBYEAR > 2017 AND PUBYEAR < 2023'
    assert params["count"] == "3" and params["start"] == "2"


def test_lookup_uses_exact_scopus_id_query_and_bad_requests_fail_loudly():
    connector, seen = make(lambda request: {"search-results": {"entry": [entry()]}})
    assert connector.get_by_id("2-s2.0-123").external_id == "123"
    assert seen[0].url.params["query"] == "SCOPUS_ID(123)"
    with pytest.raises(ConnectorError, match="numeric"):
        connector.get_by_id("10.1000/test")
    with pytest.raises(ConnectorError, match="Unsupported"):
        connector.search(SearchRequest(query="x", filters={"is_oa": True}))
    with pytest.raises(ConnectorError, match="unexpected"):
        make(lambda request: {"unexpected": []})[0].search(SearchRequest(query="x"))


def test_offset_limit_and_unsupported_capabilities_are_explicit():
    connector, _ = make(lambda request: {"search-results": {"entry": []}})
    with pytest.raises(ConnectorError, match="5,000"):
        connector.search(SearchRequest(query="x", cursor=str(MAX_OFFSET)))
    with pytest.raises(NotSupportedError):
        connector.get_citations("123")


def test_last_page_reports_the_api_result_window_cap():
    connector, _ = make(
        lambda request: {"search-results": {"opensearch:totalResults": "10000", "entry": [entry()]}}
    )
    page = connector.search(SearchRequest(query="x", limit=1, cursor=str(MAX_OFFSET - 1)))
    assert page.next_cursor is None and connector.last_search_capped is True
