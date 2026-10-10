"""Plan M1.4.7: Web of Science Starter API connector. Offline only."""

import httpx
import pytest
from pydantic import SecretStr

from app.connectors import Connector, ConnectorError, NotSupportedError, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.connectors.web_of_science import BASE_URL, MAX_PAGES, WebOfScienceConnector, to_record


def hit(uid="WOS:000123456700001", **overrides):
    value = {
        "uid": uid,
        "title": "Research paper",
        "identifiers": {"doi": "10.1000/TEST"},
        "source": {"sourceTitle": "Journal of Tests", "publishYear": "2021"},
        "names": {"authors": [{"displayName": "Ada Lovelace"}]},
        "abstract": "An abstract.",
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
        "web_of_science", BASE_URL, HttpPolicy(max_retries=0), transport=httpx.MockTransport(inner)
    )
    return WebOfScienceConnector(http=http), seen


def test_it_satisfies_connector_protocol_and_requires_a_key():
    assert isinstance(make(lambda request: {})[0], Connector)
    with pytest.raises(ConnectorError, match="WEB_OF_SCIENCE_API_KEY"):
        WebOfScienceConnector()
    connector = WebOfScienceConnector(api_key=SecretStr("key"))
    assert connector._http._client.headers["X-ApiKey"] == "key"


def test_normalizes_hit_and_rejects_missing_required_fields():
    record = to_record(hit())
    assert record is not None
    assert record.external_id == "WOS:000123456700001" and record.title == "Research paper"
    assert record.doi == "10.1000/test" and record.year == 2021
    assert record.authors == ("Ada Lovelace",) and record.venue == "Journal of Tests"
    assert to_record(hit(uid="bad")) is None
    degraded = to_record(hit(identifiers={"doi": "bad"}, abstract=""))
    assert degraded.quality_flags == ("invalid_doi", "no_abstract")


def test_search_uses_topic_syntax_filters_page_number_and_total():
    connector, seen = make(
        lambda request: {
            "hits": [hit(), hit(uid="WOS:000123456700002")],
            "metadata": {"total": 60},
        }
    )
    page = connector.search(
        SearchRequest(query='TS=("remote work")', limit=2, cursor="3", filters={"year_from": "2018", "year_to": "2022"})
    )
    assert len(page.records) == 2 and page.total == 60 and page.next_cursor == "4"
    params = seen[0].url.params
    assert params["db"] == "WOS" and params["q"] == '(TS=("remote work")) AND PY>=2018 AND PY<=2022'
    assert params["limit"] == "2" and params["page"] == "3"


def test_lookup_filters_to_exact_wos_uid_and_rejects_invalid_requests():
    connector, seen = make(lambda request: hit())
    assert connector.get_by_id("WOS:000123456700001").title == "Research paper"
    assert seen[0].url.path.endswith("/documents/WOS:000123456700001")
    missing, _ = make(lambda request: httpx.Response(404, json={}))
    assert missing.get_by_id("WOS:000123456700001") is None
    with pytest.raises(ConnectorError, match="WOS:"):
        connector.get_by_id("10.1000/test")
    with pytest.raises(ConnectorError, match="Unsupported"):
        connector.search(SearchRequest(query="x", filters={"language": "English"}))
    with pytest.raises(ConnectorError, match="unexpected"):
        make(lambda request: {"metadata": {}})[0].search(SearchRequest(query="x"))


def test_page_window_and_unsupported_capabilities_are_explicit():
    connector, _ = make(lambda request: {"hits": []})
    with pytest.raises(ConnectorError, match="5,000"):
        connector.search(SearchRequest(query="x", cursor=str(MAX_PAGES + 1)))
    with pytest.raises(NotSupportedError):
        connector.get_references("WOS:000123456700001")


def test_last_page_reports_the_conservative_result_window_cap():
    connector, _ = make(lambda request: {"hits": [hit()], "metadata": {"total": 10000}})
    page = connector.search(SearchRequest(query="x", limit=50, cursor=str(MAX_PAGES)))
    assert page.next_cursor is None and connector.last_search_capped is True
