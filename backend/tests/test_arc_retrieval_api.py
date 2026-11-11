"""retrieval_api: auth, server-side caps, 502 on failure, PDFContent.path serialisation, DOI derivation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from arc_retrieval import retrieval_api
from arc_retrieval.retrieval_api import (
    MAX_CRAWL_URLS,
    MAX_FIELD_CHARS,
    MAX_ITEMS_PER_KIND,
    MAX_SCHOLAR_RESULTS,
    MAX_WEB_RESULTS,
    create_retrieval_app,
)
from arc_retrieval.web.agent import WebSearchAgentResult
from arc_retrieval.web.crawler import CrawlResult
from arc_retrieval.web.pdf_extractor import PDFContent
from arc_retrieval.web.search import SearchResult

TOKEN = "s3cret-token-of-16+chars"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BODY = {"topic": "female labour participation"}


class FakeAgent:
    """Replaces WebSearchAgent; records constructor args and the topic searched."""

    instances: list["FakeAgent"] = []
    result: WebSearchAgentResult | None = None
    error: Exception | None = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.topics: list[str] = []
        FakeAgent.instances.append(self)

    def search_and_extract(self, topic):
        self.topics.append(topic)
        if FakeAgent.error:
            raise FakeAgent.error
        return FakeAgent.result or WebSearchAgentResult(topic=topic)


@pytest.fixture(autouse=True)
def fake_agent(monkeypatch):
    FakeAgent.instances, FakeAgent.result, FakeAgent.error = [], None, None
    monkeypatch.setattr(retrieval_api, "WebSearchAgent", FakeAgent)
    return FakeAgent


@pytest.fixture
def client():
    return TestClient(create_retrieval_app(TOKEN), raise_server_exceptions=False)


# --- auth -----------------------------------------------------------------


def test_retrieve_requires_token(client, fake_agent):
    assert client.post("/v1/retrieve", json=BODY).status_code == 401
    assert client.post("/v1/retrieve", json=BODY, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert fake_agent.instances == []  # nothing was fetched for an unauthenticated caller


def test_health_needs_no_token(client):
    assert client.get("/api/health").json() == {"status": "ok"}


@pytest.mark.parametrize("token", ["", "short", "x" * 15])
def test_service_refuses_to_start_without_a_strong_token(token, monkeypatch):
    monkeypatch.delenv("ARC_RETRIEVAL_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        create_retrieval_app(token)


def test_token_is_read_from_environment(monkeypatch):
    monkeypatch.setenv("ARC_RETRIEVAL_TOKEN", TOKEN)
    assert TestClient(create_retrieval_app()).post("/v1/retrieve", json=BODY, headers=AUTH).status_code == 200


def test_docs_endpoints_are_not_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, headers=AUTH).status_code == 404


# --- request validation & server-side caps ----------------------------------


@pytest.mark.parametrize(
    "body",
    [{}, {"topic": "ab"}, {"topic": "x" * 2001}, {**BODY, "max_web_results": -1}, {**BODY, "max_crawl_urls": "many"}],
)
def test_invalid_requests_are_422(client, body, fake_agent):
    assert client.post("/v1/retrieve", json=body, headers=AUTH).status_code == 422
    assert fake_agent.instances == []


def test_caller_limits_are_capped_server_side(client, fake_agent):
    body = {**BODY, "max_web_results": 1000, "max_scholar_results": 1000, "max_crawl_urls": 1000}
    assert client.post("/v1/retrieve", json=body, headers=AUTH).status_code == 200
    (agent,) = fake_agent.instances
    assert agent.kwargs["max_web_results"] == MAX_WEB_RESULTS
    assert agent.kwargs["max_scholar_results"] == MAX_SCHOLAR_RESULTS
    assert agent.kwargs["max_crawl_urls"] == MAX_CRAWL_URLS
    assert agent.topics == [BODY["topic"]]


def test_smaller_caller_limits_are_respected(client, fake_agent):
    body = {**BODY, "max_web_results": 2, "max_scholar_results": 0, "max_crawl_urls": 1}
    client.post("/v1/retrieve", json=body, headers=AUTH)
    kwargs = fake_agent.instances[0].kwargs
    assert (kwargs["max_web_results"], kwargs["max_scholar_results"], kwargs["max_crawl_urls"]) == (2, 0, 1)


def test_response_items_and_fields_are_truncated(client, fake_agent):
    long = "a" * (MAX_FIELD_CHARS + 500)
    n = MAX_ITEMS_PER_KIND + 10
    fake_agent.result = WebSearchAgentResult(
        topic="t",
        web_results=[SearchResult(title=f"w{i}", url=f"https://e.test/{i}", content=long) for i in range(n)],
    )
    data = client.post("/v1/retrieve", json=BODY, headers=AUTH).json()
    assert len(data["web_results"]) == MAX_ITEMS_PER_KIND
    assert len(data["web_results"][0]["content"]) == MAX_FIELD_CHARS


# --- serialisation ------------------------------------------------------------


def test_pdf_url_comes_from_path_and_failed_extractions_are_dropped(client, fake_agent):
    fake_agent.result = WebSearchAgentResult(
        topic="t",
        pdf_extractions=[
            PDFContent(path="https://e.test/a.pdf", text="body", title="A", authors=["X"], abstract="abs", success=True),
            PDFContent(path="https://e.test/bad.pdf", success=False, error="boom"),
        ],
    )
    pdfs = client.post("/v1/retrieve", json=BODY, headers=AUTH).json()["pdf_extractions"]
    assert pdfs == [
        {"url": "https://e.test/a.pdf", "title": "A", "authors": ["X"], "abstract": "abs", "text": "body", "doi": None}
    ]


def test_empty_optional_fields_become_null_and_thin_pages_are_dropped(client, fake_agent):
    fake_agent.result = WebSearchAgentResult(
        topic="t",
        scholar_papers=[SimpleNamespace(title="P", url="", authors=[], year=0, abstract="")],
        crawled_pages=[
            CrawlResult(url="https://e.test/ok", markdown="m" * 80, title="", success=True),
            CrawlResult(url="https://e.test/thin", markdown="tiny", success=True),
        ],
    )
    data = client.post("/v1/retrieve", json=BODY, headers=AUTH).json()
    assert data["scholar_papers"] == [{"title": "P", "url": None, "authors": None, "year": None, "abstract": None, "doi": None}]
    assert [c["url"] for c in data["crawled_pages"]] == ["https://e.test/ok"]
    assert data["crawled_pages"][0]["title"] is None


# --- DOI (research-main M0.2.2a) ------------------------------------------------


@pytest.mark.parametrize(
    "url,doi",
    [
        ("https://doi.org/10.1000/XYZ.123", "10.1000/xyz.123"),
        ("http://dx.doi.org/10.1000%2Fenc", "10.1000/enc"),
        ("https://onlinelibrary.wiley.com/doi/abs/10.1111/joop.12345?af=R", "10.1111/joop.12345"),
        ("https://www.tandfonline.com/doi/full/10.1080/0001.2020.1/abstract", "10.1080/0001.2020.1"),
        ("https://dl.acm.org/doi/10.1145/3290605.3300233", "10.1145/3290605.3300233"),
        ("https://link.springer.com/article/10.1007/s11135-020-01012-3", "10.1007/s11135-020-01012-3"),
        ("https://link.springer.com/chapter/10.1007/978-3-030-12345-6_5/fulltext.html", "10.1007/978-3-030-12345-6_5"),
        ("https://link.springer.com/content/pdf/10.1007/s111.pdf", "10.1007/s111"),
        # No DOI, or nothing that safely identifies this paper's DOI:
        ("https://www.sciencedirect.com/science/article/pii/S0001879120300011", None),
        ("https://x.test/paper?doi=10.1000/abc", None),
        ("https://x.test/cites/10.1000/abc", None),
        ("https://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.1.1", None),
        ("https://doi.org/not-a-doi", None),
        ("javascript:alert('10.1000/x')", None),
        ("", None),
        (None, None),
    ],
)
def test_doi_is_taken_only_from_urls_that_identify_the_paper(url, doi):
    assert retrieval_api._doi_from_url(url) == doi


@pytest.mark.parametrize(
    "metadata,doi",
    [
        ({"subject": "J. Econ 12 (2020) 1-9. doi:10.1016/j.jeco.2020.01.002."}, "10.1016/j.jeco.2020.01.002"),
        ({"keywords": "labour; https://doi.org/10.1000/XYZ"}, "10.1000/xyz"),
        ({"DOI": "10.5555/abc"}, "10.5555/abc"),
        ({"subject": "two: 10.1000/a and 10.1000/b"}, None),  # ambiguous -> none
        ({"subject": "110.1000/x"}, None),
        ({"title": "10.1000/in-the-title"}, None),  # only doi/subject/keywords count
        ({}, None),
        (None, None),
    ],
)
def test_doi_from_pdf_metadata(metadata, doi):
    assert retrieval_api._doi_from_pdf_metadata(metadata) == doi


def test_response_carries_doi_for_scholar_and_pdf_items_but_never_from_body_text(client, fake_agent):
    fake_agent.result = WebSearchAgentResult(
        topic="t",
        scholar_papers=[
            SimpleNamespace(title="S", url="https://doi.org/10.1000/s1", authors=[], year=0, abstract=""),
            SimpleNamespace(title="N", url="https://x.test/p", authors=[], year=0, abstract="cites 10.1000/zzz"),
        ],
        pdf_extractions=[
            PDFContent(path="https://x.test/a.pdf", text="References: 10.9999/cited", success=True,
                       metadata={"subject": "doi:10.1000/meta"}),
            PDFContent(path="https://link.springer.com/content/pdf/10.1007/fromurl.pdf", text="t", success=True),
            PDFContent(path="https://x.test/b.pdf", text="see doi:10.9999/cited", abstract="10.9999/x", success=True),
        ],
    )
    data = client.post("/v1/retrieve", json=BODY, headers=AUTH).json()
    assert [p["doi"] for p in data["scholar_papers"]] == ["10.1000/s1", None]
    assert [d["doi"] for d in data["pdf_extractions"]] == ["10.1000/meta", "10.1007/fromurl", None]


# --- failure ----------------------------------------------------------------


def test_agent_failure_is_502_without_leaking_details(client, fake_agent):
    fake_agent.error = RuntimeError("secret internal path /srv/x")
    response = client.post("/v1/retrieve", json=BODY, headers=AUTH)
    assert response.status_code == 502
    assert response.json() == {"detail": "Retrieval failed"}
    assert "secret" not in response.text
