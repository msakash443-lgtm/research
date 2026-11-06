import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.connectors.base import ConnectorError, PaperRecord, SearchPage
from app.landscape_scan import summarise
from app.main import app
from app.routers import idea_brief


def _paper(external_id, title, year=None, cited=None):
    return PaperRecord(connector="openalex", external_id=external_id, title=title, year=year, cited_by_count=cited)


def test_top_cited_sorts_by_count_and_leaves_uncounted_out():
    records = [_paper("W1", "Alpha", 2020, 5), _paper("W2", "Beta", 2021, None), _paper("W3", "Gamma", 2019, 50)]
    scan = summarise(records, reviews_since=2000)
    assert [e.external_id for e in scan.top_cited_in_sample] == ["W3", "W1"]  # W2 has no count: not read as zero, not listed
    assert scan.uncounted == 1


def test_ties_sort_by_external_id_so_output_is_stable():
    records = [_paper("W9", "Same", 2020, 7), _paper("W2", "Same", 2020, 7)]
    assert [e.external_id for e in summarise(records, reviews_since=2000).top_cited_in_sample] == ["W2", "W9"]


def test_reviews_are_recent_and_found_by_title_only():
    records = [
        _paper("W1", "A systematic review of trust", 2024),
        _paper("W2", "A systematic review of trust (old)", 2010),  # too old for the window
        _paper("W3", "Trust in teams", 2025),  # no review in title
        _paper("W4", "Meta-analyses of trust effects", 2023),
        _paper("W5", "Reviewing the reviewers", None),  # undated: never listed as recent
    ]
    scan = summarise(records, reviews_since=2020)
    assert [e.external_id for e in scan.recent_reviews] == ["W1", "W4"]


def test_volume_by_year_and_undated_count():
    records = [_paper("W1", "a", 2021), _paper("W2", "b", 2020), _paper("W3", "c", 2021), _paper("W4", "d", None)]
    scan = summarise(records, reviews_since=2000)
    assert scan.volume_by_year_in_sample == {2020: 1, 2021: 2}
    assert scan.undated == 1
    assert scan.sample_size == 4


def test_top_n_caps_both_lists():
    records = [_paper(f"W{i}", f"Review {i}", 2024, i) for i in range(20)]
    scan = summarise(records, reviews_since=2020, top_n=3)
    assert len(scan.top_cited_in_sample) == 3
    assert len(scan.recent_reviews) == 3



def test_the_same_work_returned_twice_is_counted_once_and_keeps_the_better_record():
    title = "Health and wellbeing effects of working from home: an overview of systematic reviews"
    records = [_paper("W1", title, 2026, None), _paper("W2", title, 2026, 7), _paper("W3", "A different long enough title here", 2025, 1)]
    scan = summarise(records, reviews_since=2020)
    assert scan.sample_size == 2 and scan.duplicates_removed == 1
    assert [e.external_id for e in scan.recent_reviews] == ["W2"]
    assert scan.volume_by_year_in_sample == {2025: 1, 2026: 1}
    assert scan.uncounted == 0  # the uncounted copy is the one dropped


def test_records_sharing_a_doi_are_one_work_but_different_dois_are_not():
    same = [
        PaperRecord(connector="openalex", external_id="W1", title="x", doi="10.1234/abc", year=2020, cited_by_count=1),
        PaperRecord(connector="openalex", external_id="W2", title="y", doi="10.1234/ABC", year=2020, cited_by_count=2),
    ]
    assert summarise(same, reviews_since=2000).sample_size == 1
    title = "An identical long enough title shared by two papers"
    different = [
        PaperRecord(connector="openalex", external_id="W1", title=title, doi="10.1234/a", year=2020),
        PaperRecord(connector="openalex", external_id="W2", title=title, doi="10.1234/b", year=2020),
    ]
    assert summarise(different, reviews_since=2000).sample_size == 2

# --- route -----------------------------------------------------------------------------------------------------------


class FakeConnector:
    def __init__(self, records, total=None, error=None):
        self.records = records
        self.total = total
        self.error = error
        self.requests = []

    def search(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return SearchPage(records=tuple(self.records), total=self.total)


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/api/auth/development/login", json={"email": f"ls-{uuid.uuid4().hex}@example.com", "display_name": "L"})
    return c


@pytest.fixture
def enabled(monkeypatch):
    """Only OpenAlex is switched on in these tests; no network."""
    monkeypatch.setattr(idea_brief, "is_enabled", lambda name, enabled_list, allow: name == "openalex")
    holder = {}

    def use(connector):
        holder["connector"] = connector
        monkeypatch.setattr(idea_brief, "build_connector", lambda name: holder["connector"])
        return connector

    return use


def _project(client: TestClient) -> str:
    return client.post("/api/projects", json={"title": "Landscape"}).json()["id"]


def test_scan_reports_what_the_search_returned(client, enabled):
    pid = _project(client)
    this_year = datetime.now().year
    fake = enabled(
        FakeConnector(
            [
                _paper("W1", f"Systematic review of trust {this_year - 1}", this_year - 1, 90),
                _paper("W2", "Trust in teams", this_year - 2, 12),
                _paper("W3", "Trust, uncounted", None, None),
            ],
            total=1234,
        )
    )
    body = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": " trust teams ", "limit": 10}).json()
    assert body["connector"] == "openalex"
    assert body["query"] == "trust teams"  # stripped
    assert body["total_reported"] == 1234
    assert body["reviews_since"] == this_year - 5
    assert body["top_cited_in_sample"][0]["external_id"] == "W1"
    assert body["recent_reviews"][0]["external_id"] == "W1"
    assert body["uncounted"] == 1
    assert body["volume_by_year_in_sample"][str(this_year - 2)] == 1
    assert "not the whole literature" in body["note"]
    assert "within the returned sample" in body["note"]
    assert fake.requests[0].query == "trust teams"
    assert fake.requests[0].limit == 10


def test_no_enabled_connector_is_refused_without_a_search(client, monkeypatch):
    pid = _project(client)
    monkeypatch.setattr(idea_brief, "is_enabled", lambda *args: False)
    called = []
    monkeypatch.setattr(idea_brief, "build_connector", lambda name: called.append(name))
    response = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust"})
    assert response.status_code == 503
    assert called == []


def test_blank_query_gives_an_empty_scan_without_a_search(client, enabled):
    pid = _project(client)
    fake = enabled(FakeConnector([_paper("W1", "Trust", 2020, 3)]))
    body = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "   "}).json()
    assert body["connector"] is None
    assert body["top_cited_in_sample"] == [] and body["sample_size"] == 0
    assert fake.requests == []


def test_connector_failure_is_a_bad_gateway_not_an_empty_scan_and_does_not_echo_the_error(client, enabled):
    pid = _project(client)
    enabled(FakeConnector([], error=ConnectorError("service down https://x.example/?api_key=SECRET123")))
    response = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust"})
    assert response.status_code == 502
    assert "SECRET123" not in response.text


def test_the_review_cutoff_year_is_the_utc_year(client, enabled, monkeypatch):
    class FakeDatetime:
        @staticmethod
        def now(tz=None):
            return datetime(2031 if tz is not None else 2030, 6, 1)

    monkeypatch.setattr(idea_brief, "datetime", FakeDatetime)
    pid = _project(client)
    enabled(FakeConnector([]))
    body = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust"}).json()
    assert body["reviews_since"] == 2031 - 5


def test_limit_above_the_cap_is_refused(client, enabled):
    pid = _project(client)
    enabled(FakeConnector([]))
    response = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust", "limit": 26})
    assert response.status_code == 422
