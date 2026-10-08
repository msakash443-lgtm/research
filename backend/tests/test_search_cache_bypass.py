"""A database search is always live, even when the connector cache is on (plan M1.3.7)."""
import httpx
import pytest

from app.connectors.http import ConnectorHttpClient, HttpPolicy, bypass_cache
from app.connectors.openalex import OpenAlexConnector
from app.config import get_settings
from app.search_query import BooleanQuery, ConceptBlock
from app.search_runner import run_search
from tests.test_search_runner import setup  # noqa: F401  (creates a project in a session)

QUERY = BooleanQuery(blocks=(ConceptBlock(label="a", terms=("remote work",)),))
WORK = {"id": "https://openalex.org/W1", "display_name": "A sufficiently long distinct title about remote work", "publication_year": 2020}


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "connectors_enabled", ["openalex"])


def _connector(counter):
    def respond(request):
        counter.append(request)
        return httpx.Response(200, json={"results": [WORK], "meta": {"count": 1, "next_cursor": None}})

    http = ConnectorHttpClient("openalex", "https://api.test", HttpPolicy(cache_ttl_seconds=600), transport=httpx.MockTransport(respond))
    return OpenAlexConnector(http=http), http


def test_two_identical_searches_each_reach_the_network_even_with_the_cache_on():
    calls = []
    connector, http = _connector(calls)
    db, project = setup()
    first = run_search(db, project=project, actor="u", connector=connector, query=QUERY)
    second = run_search(db, project=project, actor="u", connector=connector, query=QUERY)
    assert len(calls) == 2 and http.cache_hits == 0
    assert first.counts["cache_bypassed"] is True and second.counts["cache_bypassed"] is True


def test_the_bypass_applies_only_inside_the_block():
    calls = []
    _, http = _connector(calls)
    http.get_json("/works", {"q": "x"})
    http.get_json("/works", {"q": "x"})
    assert len(calls) == 1
    with bypass_cache():
        http.get_json("/works", {"q": "x"})
    assert len(calls) == 2
    http.get_json("/works", {"q": "x"})
    assert len(calls) == 2  # served from the entry the bypassed call refreshed
