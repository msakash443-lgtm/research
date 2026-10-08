"""Plan M1.4.6: CORE connector (API v3). Offline only."""

import httpx
import pytest
from pydantic import SecretStr

from app.connectors import Connector, ConnectorError, NotSupportedError, SearchRequest
from app.connectors.core import MAX_FULLTEXT_CHARS, MAX_OFFSET, CoreConnector, to_record, work_id
from app.connectors.http import ConnectorHttpClient, HttpPolicy


def work(n=1, **over):
    base = {
        "id": n,
        "title": f"Open paper {n}",
        "doi": f"https://doi.org/10.1000/C{n}",
        "authors": [{"name": "Ada Lovelace"}, {"name": " "}],
        "yearPublished": 2019,
        "journals": [{"title": "Journal of Open Things"}],
        "abstract": "An abstract.",
        "downloadUrl": "https://core.ac.uk/download/1.pdf",
        "links": [{"type": "display", "url": "https://core.ac.uk/works/1"}],
    }
    base.update(over)
    return base


def make(handler):
    seen = []

    def inner(request):
        seen.append(request)
        result = handler(request)
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)

    http = ConnectorHttpClient("core", "https://api.core.ac.uk/v3", HttpPolicy(max_retries=0), transport=httpx.MockTransport(inner))
    return CoreConnector(http), seen


def test_it_satisfies_the_connector_protocol():
    assert isinstance(make(lambda r: {})[0], Connector)


def test_a_key_is_required_and_sent_as_bearer(monkeypatch):
    with pytest.raises(ConnectorError, match="API key"):
        CoreConnector()
    with pytest.raises(ConnectorError, match="API key"):
        CoreConnector(api_key=SecretStr("  "))
    conn = CoreConnector(api_key=SecretStr("secret-key"))
    assert conn._http._client.headers["Authorization"] == "Bearer secret-key"


def test_ids():
    assert work_id("123") == work_id("https://core.ac.uk/works/123") == work_id("https://core.ac.uk/download/123") == "123"
    for bad in ("", "W1", "10.1000/x"):
        with pytest.raises(ConnectorError):
            work_id(bad)


def test_work_normalisation():
    r = to_record(work(7))
    assert (r.connector, r.external_id, r.title, r.doi) == ("core", "7", "Open paper 7", "10.1000/c7")
    assert r.authors == ("Ada Lovelace",) and r.year == 2019 and r.venue == "Journal of Open Things"
    assert r.url == "https://core.ac.uk/works/1" and r.oa_url == "https://core.ac.uk/download/1.pdf"
    assert r.source_ids == {"core": "7"} and r.quality_flags == ()
    degraded = to_record(work(8, doi="nope", abstract=None, journals=[], publisher="Pub", links=[], downloadUrl="javascript:x", yearPublished=99))
    assert degraded.quality_flags == ("invalid_doi", "no_abstract") and degraded.venue == "Pub"
    assert degraded.url == "https://core.ac.uk/works/8" and degraded.oa_url is None and degraded.year is None
    assert to_record(work(9, title="")) is None and to_record(work(id="x")) is None


def test_search_with_filters_and_paging():
    conn, seen = make(lambda r: {"totalHits": 3, "results": [work(1), work(2), {"id": 3}]})
    page = conn.search(SearchRequest(query='("remote work") AND (wellbeing)', limit=3, filters={"year_from": 2015, "year_to": 2020}))
    assert [r.external_id for r in page.records] == ["1", "2"] and conn.skipped_records == 1
    assert page.total == 3 and page.next_cursor is None
    params = seen[0].url.params
    assert params["q"] == '(("remote work") AND (wellbeing)) AND yearPublished>=2015 AND yearPublished<=2020'
    assert params["limit"] == "3" and params["offset"] == "0"


def test_cursor_advances_and_the_offset_window_is_reported():
    conn, _ = make(lambda r: {"totalHits": 50000, "results": [work(i) for i in range(10)]})
    page = conn.search(SearchRequest(query="x", limit=10, cursor="20"))
    assert page.next_cursor == "30" and conn.last_search_capped is False
    page = conn.search(SearchRequest(query="x", limit=10, cursor=str(MAX_OFFSET - 10)))
    assert page.next_cursor is None and conn.last_search_capped is True
    with pytest.raises(ConnectorError, match="10,000"):
        conn.search(SearchRequest(query="x", cursor=str(MAX_OFFSET)))


def test_bad_filters_and_replies():
    conn, _ = make(lambda r: ["not", "a", "dict"])
    with pytest.raises(ConnectorError, match="Unsupported"):
        conn.search(SearchRequest(query="x", filters={"is_oa": True}))
    with pytest.raises(ConnectorError, match="four-digit"):
        conn.search(SearchRequest(query="x", filters={"year_from": True}))
    with pytest.raises(ConnectorError, match="unexpected"):
        conn.search(SearchRequest(query="x"))


def test_get_by_id():
    conn, seen = make(lambda r: work(5) if r.url.path.endswith("/works/5") else httpx.Response(404, json={}))
    assert conn.get_by_id("5").title == "Open paper 5" and seen[0].url.path == "/v3/works/5"
    assert conn.get_by_id("6") is None


def test_fulltext_licence_is_never_guessed_and_oversize_is_refused():
    conn, _ = make(lambda r: work(5, fullText="The whole paper.") if r.url.path.endswith("/5") else work(6, fullText=""))
    text = conn.get_fulltext("5")
    assert text.text == "The whole paper." and text.license is None and text.source_url == "https://core.ac.uk/download/1.pdf"
    assert conn.get_fulltext("6") is None
    big, _ = make(lambda r: work(7, fullText="x" * (MAX_FULLTEXT_CHARS + 1)))
    with pytest.raises(ConnectorError, match="refused"):
        big.get_fulltext("7")


def test_citation_data_is_not_offered():
    conn, _ = make(lambda r: {})
    with pytest.raises(NotSupportedError):
        conn.get_references("1")
    with pytest.raises(NotSupportedError):
        conn.get_citations("1")
