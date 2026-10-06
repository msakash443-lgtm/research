import json

import httpx
import pytest
from pydantic import SecretStr

from app.connectors import Connector, ConnectorError, NotSupportedError, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.connectors.semantic_scholar import SemanticScholarConnector, paper_ref, to_record


def pid(n):
    return f"{n:040x}"


def paper(n=1, **over):
    base = {
        "paperId": pid(n),
        "title": f"Paper {n}",
        "abstract": "An abstract.",
        "year": 2020,
        "venue": "J. Testing",
        "authors": [{"name": "Ada Lovelace"}, {"name": " "}, {"authorId": None}],
        "externalIds": {"DOI": f"10.1000/P{n}", "ArXiv": "2001.00001", "PubMed": 77, "CorpusId": 5},
        "openAccessPdf": {"url": "https://oa.test/p.pdf"},
        "url": f"https://www.semanticscholar.org/paper/{pid(n)}",
    }
    base.update(over)
    return base


class Server:
    def __init__(self, handler):
        self.requests = []
        self.handler = handler

    def __call__(self, request):
        self.requests.append(request)
        result = self.handler(request)
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)


def connector(handler, **kwargs):
    server = Server(handler)
    http = ConnectorHttpClient("semantic_scholar", "https://api.s2.test/graph/v1", HttpPolicy(max_retries=0), transport=httpx.MockTransport(server))
    return SemanticScholarConnector(http, **kwargs), server


def test_it_satisfies_the_connector_protocol():
    assert isinstance(connector(lambda r: {})[0], Connector)


def test_paper_normalisation():
    record = to_record(paper())
    assert (record.connector, record.external_id, record.title) == ("semantic_scholar", pid(1), "Paper 1")
    assert record.doi == "10.1000/p1" and record.authors == ("Ada Lovelace",)
    assert (record.year, record.venue, record.abstract) == (2020, "J. Testing", "An abstract.")
    assert record.oa_url == "https://oa.test/p.pdf" and record.url.startswith("https://www.semanticscholar.org/")
    assert record.source_ids == {"semantic_scholar": pid(1), "arxiv": "2001.00001", "pmid": "77", "corpusid": "5"}
    assert record.quality_flags == ()


def test_flags_and_degraded_fields():
    record = to_record(paper(abstract=None, venue="arXiv.org", openAccessPdf=None, externalIds={"DOI": "bad"}, year="2020"))
    assert set(record.quality_flags) == {"preprint", "no_abstract", "invalid_doi"}
    assert record.oa_url is None and record.doi is None and record.year is None


@pytest.mark.parametrize("bad", [{"paperId": None}, {"paperId": "short"}, {"title": None}, {"title": " "}])
def test_stubs_are_skipped_and_counted(bad):
    assert to_record(paper(**bad)) is None
    c, _ = connector(lambda r: {"total": 2, "data": [paper(1, **bad), paper(2)]})
    assert [r.external_id for r in c.search(SearchRequest(query="x")).records] == [pid(2)]
    assert c.skipped_records == 1


def test_search_params_paging_and_total():
    c, server = connector(lambda r: {"total": 500, "offset": 0, "next": 10, "data": [paper(1)]})
    page = c.search(SearchRequest(query="labour", limit=10, filters={"year_from": 2015, "year_to": 2020, "type": "JournalArticle", "is_oa": True}))
    params = server.requests[0].url.params
    assert (params["query"], params["limit"], params["offset"]) == ("labour", "10", "0")
    assert params["year"] == "2015-2020" and params["publicationTypes"] == "JournalArticle" and "openAccessPdf" in params
    assert "paperId" in params["fields"]
    assert (page.total, page.next_cursor) == (500, "10")

    c.search(SearchRequest(query="x", limit=10, cursor="10", filters={"year_from": 2018}))
    assert server.requests[1].url.params["offset"] == "10" and server.requests[1].url.params["year"] == "2018-"
    c.search(SearchRequest(query="x", filters={"year_to": 2001}))
    assert server.requests[2].url.params["year"] == "-2001"


def test_last_page_has_no_cursor_and_missing_data_is_empty():
    c, _ = connector(lambda r: {"total": 0})
    page = c.search(SearchRequest(query="x"))
    assert page.records == () and page.next_cursor is None


def test_search_stops_at_the_apis_1000_window_and_says_so():
    c, server = connector(lambda r: {"total": 5000, "next": 1000, "data": [paper(1)]})
    page = c.search(SearchRequest(query="x", limit=100, cursor="900"))
    assert page.next_cursor is None and c.last_search_capped is True
    assert server.requests[0].url.params["limit"] == "100"
    with pytest.raises(ConnectorError):
        c.search(SearchRequest(query="x", cursor="1000"))
    c.search(SearchRequest(query="x", limit=100, cursor="950"))
    assert server.requests[-1].url.params["limit"] == "50"  # window trimmed, never exceeded


@pytest.mark.parametrize(
    "request_",
    [
        SearchRequest(query="x", filters={"fieldsOfStudy": "Biology"}),
        SearchRequest(query="x", filters={"year_from": "abcd"}),
        SearchRequest(query="x", filters={"type": "a&b=1"}),
        SearchRequest(query="x", filters={"is_oa": False}),
        SearchRequest(query="x", cursor="-5"),
        SearchRequest(query="x", cursor="abc"),
    ],
)
def test_bad_filters_and_cursors_fail_before_any_request(request_):
    c, server = connector(lambda r: {})
    with pytest.raises(ConnectorError):
        c.search(request_)
    assert server.requests == []


def test_search_limit_is_capped_at_100():
    c, server = connector(lambda r: {"data": []})
    c.search(SearchRequest(query="x", limit=200))
    assert server.requests[0].url.params["limit"] == "100"


def test_malformed_reply_fails_loudly():
    c, _ = connector(lambda r: [])
    with pytest.raises(ConnectorError):
        c.search(SearchRequest(query="x"))
    c2, _ = connector(lambda r: {"data": "x"})
    with pytest.raises(ConnectorError):
        c2.search(SearchRequest(query="x"))


@pytest.mark.parametrize(
    "value,expected",
    [(pid(1).upper(), pid(1)), ("10.1000/ABC", "DOI:10.1000/abc"), ("https://doi.org/10.1000/abc", "DOI:10.1000/abc")],
)
def test_paper_ref_accepts_ids_and_dois(value, expected):
    assert paper_ref(value) == expected


@pytest.mark.parametrize("value", ["", "abc", "W123", "../../graph", pid(1) + "/citations", "ARXIV:2001.1"])
def test_paper_ref_refuses_everything_else(value):
    with pytest.raises(ConnectorError):
        paper_ref(value)


def test_get_by_id_none_for_missing_and_error_for_failure():
    c, server = connector(lambda r: paper(1) if r.url.path.endswith(pid(1)) else httpx.Response(404, json={"error": "x"}))
    assert c.get_by_id(pid(1)).external_id == pid(1)
    assert c.get_by_id(pid(2)) is None
    c.get_by_id("10.1000/a?x#y")
    assert server.requests[-1].url.raw_path.startswith(b"/graph/v1/paper/DOI:10.1000/a%3Fx%23y")
    broken, _ = connector(lambda r: httpx.Response(500, json={}))
    with pytest.raises(ConnectorError):
        broken.get_by_id(pid(1))
    stub, _ = connector(lambda r: {"paperId": pid(1), "title": None})
    with pytest.raises(ConnectorError):
        stub.get_by_id(pid(1))


def test_get_many_aligns_with_input_and_marks_missing():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return [paper(1), None, paper(3)]

    c, server = connector(handler)
    result = c.get_many([pid(1), pid(2), "10.1000/x"])
    assert [r.external_id if r else None for r in result] == [pid(1), None, pid(3)]
    assert seen["body"] == {"ids": [pid(1), pid(2), "DOI:10.1000/x"]}
    assert server.requests[0].method == "POST"


def test_get_many_validates_before_calling_and_checks_the_reply():
    c, server = connector(lambda r: [paper(1)])
    assert c.get_many([]) == ()
    with pytest.raises(ConnectorError):
        c.get_many(["nonsense"])
    with pytest.raises(ConnectorError):
        c.get_many([pid(i) for i in range(1, 502)])
    assert server.requests == []
    with pytest.raises(ConnectorError):
        c.get_many([pid(1), pid(2)])  # reply length mismatch


def test_references_and_citations_page_and_unwrap_the_right_key():
    pages = {
        0: {"offset": 0, "next": 2, "data": [{"citedPaper": paper(1)}, {"citedPaper": {"paperId": None, "title": None}}]},
        2: {"offset": 2, "data": [{"citedPaper": paper(3)}]},
    }
    c, server = connector(lambda r: pages[int(r.url.params["offset"])])
    assert [r.external_id for r in c.get_references(pid(9))] == [pid(1), pid(3)]
    assert server.requests[0].url.path.endswith(f"/paper/{pid(9)}/references")
    assert c.skipped_records == 1 and c.last_related_truncated is False

    cites, server2 = connector(lambda r: {"data": [{"citingPaper": paper(4)}]})
    assert [r.external_id for r in cites.get_citations("10.1000/p9")] == [pid(4)]
    assert server2.requests[0].url.path.endswith("/paper/DOI:10.1000/p9/citations")


def test_related_results_are_capped_and_say_so():
    c, _ = connector(lambda r: {"next": int(r.url.params["offset"]) + 3, "data": [{"citedPaper": paper(i)} for i in range(1, 4)]}, max_related=5)
    assert len(c.get_references(pid(9))) == 5 and c.last_related_truncated is True


def test_related_paging_ends_on_empty_page_and_never_loops():
    calls = []

    def handler(request):
        calls.append(request)
        assert len(calls) < 4, "paging did not stop"
        return {"next": int(request.url.params["offset"]) + 99, "data": []}

    c, _ = connector(handler)
    assert c.get_citations(pid(9)) == ()


def test_unknown_paper_for_related_calls_raises_not_empty():
    c, _ = connector(lambda r: httpx.Response(404, json={}))
    for call in (c.get_references, c.get_citations):
        with pytest.raises(ConnectorError):
            call(pid(9))


def test_fulltext_not_supported():
    with pytest.raises(NotSupportedError):
        connector(lambda r: {})[0].get_fulltext(pid(1))


def test_api_key_only_travels_as_a_header():
    c = SemanticScholarConnector(api_key=SecretStr(" k-123 "))
    headers = c._http._client.headers
    assert headers["x-api-key"] == "k-123"
    assert c._http.policy.min_interval_seconds == 0.1
    anonymous = SemanticScholarConnector()
    assert "x-api-key" not in anonymous._http._client.headers
    assert anonymous._http.policy.min_interval_seconds > 1


def test_invalid_max_related():
    with pytest.raises(ValueError):
        SemanticScholarConnector(max_related=0)
