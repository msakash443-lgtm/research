import httpx
import pytest
from pydantic import SecretStr

from app.connectors import ConnectorError, NotSupportedError, SearchRequest
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.connectors.openalex import OpenAlexConnector, to_record, work_id


def work(n=1, **over):
    base = {
        "id": f"https://openalex.org/W{n}",
        "display_name": f"Paper {n}",
        "doi": f"https://doi.org/10.1000/P{n}",
        "publication_year": 2020,
        "authorships": [{"author": {"display_name": "Ada Lovelace"}}, {"author": {"display_name": " "}}],
        "primary_location": {"landing_page_url": "https://pub.test/p", "source": {"display_name": "J. Testing"}},
        "open_access": {"oa_url": "https://oa.test/p.pdf"},
        "abstract_inverted_index": {"Hello": [0], "world": [1]},
        "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/12345"},
        "type": "article",
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
    http = ConnectorHttpClient(
        "openalex", "https://api.openalex.org", HttpPolicy(max_retries=0), transport=httpx.MockTransport(server)
    )
    return OpenAlexConnector(http, **kwargs), server


@pytest.mark.parametrize("bad", [{"display_name": None, "title": None}, {"id": "nonsense"}, {"display_name": "  "}])
def test_unrepresentable_works_are_skipped_and_counted(bad):
    assert to_record(work(**bad)) is None
    c, _ = connector(lambda r: {"meta": {"count": 2}, "results": [work(1, **bad), work(2)]})
    page = c.search(SearchRequest(query="x"))
    assert [r.external_id for r in page.records] == ["W2"]
    assert c.skipped_records == 1


def test_abstract_ignores_hostile_positions():
    huge = {"a": [10**9], "b": [-1], "c": ["x"], "ok": [0]}
    assert to_record(work(abstract_inverted_index=huge)).abstract == "ok"


def test_no_contact_means_no_mailto_and_last_page_has_no_cursor():
    c, server = connector(lambda r: {"meta": {"count": 1, "next_cursor": None}, "results": []})
    page = c.search(SearchRequest(query="x"))
    assert "mailto" not in server.requests[0].url.params
    assert page.next_cursor is None and page.records == ()


def test_search_without_an_api_key_stops_after_the_first_page_and_marks_the_cap():
    c, server = connector(lambda r: {"meta": {"count": 101, "next_cursor": "requires-key"}, "results": [work(1)]})

    page = c.search(SearchRequest(query="x", limit=100))

    assert len(server.requests) == 1
    assert page.next_cursor is None
    assert c.last_search_capped is True


def test_search_with_an_api_key_keeps_the_cursor_and_sends_the_key():
    c, server = connector(
        lambda r: {"meta": {"count": 101, "next_cursor": "next-page"}, "results": [work(1)]},
        api_key=SecretStr("test-openalex-key"),
    )

    page = c.search(SearchRequest(query="x", limit=100))

    assert page.next_cursor == "next-page"
    assert c.last_search_capped is False
    assert server.requests[0].url.params["api_key"] == "test-openalex-key"


def test_malformed_search_reply_fails_loudly():
    c, _ = connector(lambda r: {"meta": {}, "results": "nope"})
    with pytest.raises(ConnectorError):
        c.search(SearchRequest(query="x"))


@pytest.mark.parametrize("value", ["W1", "w1", "https://openalex.org/W1", " W1 "])
def test_work_id_accepts_known_forms(value):
    assert work_id(value) == "W1"


@pytest.mark.parametrize("value", ["", "1", "W", "W1/../x", "W1?x=1", "https://evil.test/W1", "A123", "W1,W2"])
def test_work_id_refuses_everything_else(value):
    with pytest.raises(ConnectorError):
        work_id(value)


def test_get_by_id_server_error_is_not_none():
    c, _ = connector(lambda r: httpx.Response(500, json={}))
    with pytest.raises(ConnectorError):
        c.get_by_id("W1")


def test_get_citations_pages_through_cites_filter():
    pages = {"*": ([work(1), work(2)], "p2"), "p2": ([work(3)], None)}

    def handler(request):
        results, cursor = pages[request.url.params["cursor"]]
        return {"meta": {"next_cursor": cursor}, "results": results}

    c, server = connector(handler)
    assert [r.external_id for r in c.get_citations("W9")] == ["W1", "W2", "W3"]
    assert server.requests[0].url.params["filter"] == "cites:W9"
    assert c.last_related_truncated is False


def test_get_citations_is_capped_and_says_so():
    c, _ = connector(lambda r: {"meta": {"next_cursor": "more"}, "results": [work(i) for i in range(1, 4)]}, max_related=5)
    assert len(c.get_citations("W9")) == 5
    assert c.last_related_truncated is True


def test_get_citations_stops_on_empty_page_even_with_cursor():
    calls = []

    def handler(request):
        calls.append(request)
        assert len(calls) < 4, "paging did not stop on an empty page"
        return {"meta": {"next_cursor": "loop"}, "results": []}

    c, server = connector(handler)
    assert c.get_citations("W9") == ()
    assert len(server.requests) == 1


def test_get_references_fetches_referenced_works_in_batches():
    refs = [f"https://openalex.org/W{i}" for i in range(1, 121)] + ["https://evil.test/x"]

    def handler(request):
        if request.url.path == "/works/W9":
            return {"id": "https://openalex.org/W9", "referenced_works": refs}
        wanted = request.url.params["filter"].removeprefix("openalex:").split("|")
        return {"meta": {}, "results": [work(int(w[1:])) for w in wanted]}

    c, server = connector(handler, max_related=500)
    records = c.get_references("W9")
    assert len(records) == 120  # the foreign URL was ignored
    assert [len(r.url.params["filter"].split("|")) for r in server.requests[1:]] == [50, 50, 20]
    assert c.last_related_truncated is False


def test_get_references_cap_and_missing_work():
    refs = [f"https://openalex.org/W{i}" for i in range(1, 11)]

    def handler(request):
        if request.url.path == "/works/W9":
            return {"id": "x", "referenced_works": refs}
        wanted = request.url.params["filter"].removeprefix("openalex:").split("|")
        return {"results": [work(int(w[1:])) for w in wanted]}

    c, _ = connector(handler, max_related=4)
    assert len(c.get_references("W9")) == 4 and c.last_related_truncated is True

    gone, _ = connector(lambda r: httpx.Response(404, json={}))
    with pytest.raises(ConnectorError):
        gone.get_references("W9")  # not "this work cites nothing"


def test_fulltext_is_not_supported():
    with pytest.raises(NotSupportedError):
        connector(lambda r: {})[0].get_fulltext("W1")


