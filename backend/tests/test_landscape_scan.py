import json
import uuid
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.connectors.base import ConnectorError, PaperRecord, SearchPage
from app.connectors.crossref import CrossrefConnector
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.connectors.openalex import OpenAlexConnector
from app.database import SessionLocal
from app.landscape_scan import summarise
from app.main import app
from app.models import Artifact, LandscapeScanRun
from app.routers import idea_brief
from tests.api_helpers import create_project


def _paper(external_id, title, year=None, cited=None, connector="openalex"):
    return PaperRecord(connector=connector, external_id=external_id, title=title, year=year, cited_by_count=cited)


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
        _paper("W1", "A systematic review of trust", 2024, 1),
        _paper("W2", "A plain study of trust", 2024, 1),
        _paper("W3", "An old meta-analysis of trust", 2015, 1),
        _paper("W4", "A meta-analysis of trust", 2023, 1),
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
    scan = summarise(records, reviews_since=2000, top_n=3)
    assert len(scan.top_cited_in_sample) == 3
    assert len(scan.recent_reviews) == 3


def test_the_same_work_returned_twice_is_counted_once_and_keeps_the_better_record():
    title = "Health and wellbeing effects of working from home: an overview of systematic reviews"
    records = [_paper("W1", title, 2022, None), _paper("W2", title, 2022, 9)]
    scan = summarise(records, reviews_since=2000)
    assert scan.sample_size == 1
    assert scan.top_cited_in_sample[0].external_id == "W2"
    assert scan.uncounted == 0  # the uncounted copy is the one dropped


def test_records_sharing_a_doi_are_one_work_but_different_dois_are_not():
    same = [
        PaperRecord(connector="openalex", external_id="W1", title="A", year=2020, doi="10.1234/x"),
        PaperRecord(connector="crossref", external_id="10.1234/x", title="A different title", year=2021, doi="10.1234/x"),
    ]
    assert summarise(same, reviews_since=2000).sample_size == 1
    different = [
        PaperRecord(connector="openalex", external_id="W1", title="A", year=2020, doi="10.1234/x"),
        PaperRecord(connector="crossref", external_id="10.1234/y", title="A", year=2020, doi="10.1234/y"),
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


@pytest.fixture
def enabled_services(monkeypatch):
    """Switches on exactly the given connector names; `build_connector(name)` returns `connectors[name]`."""

    def use(connectors: dict):
        monkeypatch.setattr(idea_brief, "is_enabled", lambda name, enabled_list, allow: name in connectors)
        monkeypatch.setattr(idea_brief, "build_connector", lambda name: connectors[name])
        return connectors

    return use


def test_scan_reports_what_every_enabled_service_returned(client, enabled):
    pid = create_project(client, "Landscape")
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
    assert body["query"] == "trust teams"  # stripped
    assert body["services"] == [{"service": "openalex", "status": "ok", "count": 3, "total_reported": 1234, "error": None}]
    assert body["reviews_since"] == this_year - 5
    assert body["top_cited_in_sample"][0]["external_id"] == "W1"
    assert body["recent_reviews"][0]["external_id"] == "W1"
    assert body["uncounted"] == 1
    assert body["volume_by_year_in_sample"][str(this_year - 2)] == 1
    assert "not the whole literature" in body["note"]
    assert "de-duplicated sample" in body["note"]
    assert fake.requests[0].query == "trust teams"
    assert fake.requests[0].limit == 10


def test_every_enabled_connector_is_searched_and_a_work_found_by_two_is_counted_once(client, enabled_services):
    pid = create_project(client, "Landscape")
    openalex = FakeConnector([_paper("W1", "Trust in teams at work", 2020, 5, connector="openalex")])
    crossref = FakeConnector([_paper("10.1/t1", "Trust in teams at work", 2020, 5, connector="crossref")])
    enabled_services({"openalex": openalex, "crossref": crossref})

    body = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust"}).json()

    services = {s["service"]: s for s in body["services"]}
    assert services["openalex"] == {"service": "openalex", "status": "ok", "count": 1, "total_reported": None, "error": None}
    assert services["crossref"] == {"service": "crossref", "status": "ok", "count": 1, "total_reported": None, "error": None}
    assert body["sample_size"] == 1  # same work, found by both services
    assert body["duplicates_removed"] == 1
    assert openalex.requests and crossref.requests


def test_a_failed_service_is_listed_with_its_error_while_the_working_service_still_returns_results(client, enabled_services):
    pid = create_project(client, "Landscape")
    openalex = FakeConnector([_paper("W1", "Trust in teams", 2020, 5)])
    crossref = FakeConnector([], error=ConnectorError("service down https://x.example/?api_key=SECRET123"))
    enabled_services({"openalex": openalex, "crossref": crossref})

    response = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust"})

    assert response.status_code == 200
    assert "SECRET123" not in response.text
    body = response.json()
    services = {s["service"]: s for s in body["services"]}
    assert services["openalex"]["status"] == "ok" and services["openalex"]["count"] == 1
    assert services["crossref"]["status"] == "failed"
    assert services["crossref"]["error"]
    assert body["sample_size"] == 1  # the failed service's absence never shrinks the result to an empty scan


def test_a_failing_sole_connector_is_listed_as_failed_not_a_silently_empty_scan(client, enabled, caplog):
    pid = create_project(client, "Landscape")
    enabled(FakeConnector([], error=ConnectorError("service down https://x.example/?api_key=SECRET123")))

    with caplog.at_level("DEBUG"):
        response = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust"})

    assert response.status_code == 200
    assert "SECRET123" not in response.text
    assert "SECRET123" not in caplog.text
    body = response.json()
    assert body["services"] == [
        {"service": "openalex", "status": "failed", "count": 0, "total_reported": None,
         "error": "The literature service could not be reached or returned an error."}
    ]
    assert body["sample_size"] == 0


def test_two_real_connectors_over_mock_transport_one_fails_without_dropping_the_other(client, monkeypatch):
    """The acceptance scenario built against real connector parsing, not a test double: Crossref fails
    (a malformed reply, over `httpx.MockTransport`), OpenAlex succeeds."""
    openalex_http = ConnectorHttpClient(
        "openalex", "https://api.openalex.org", HttpPolicy(max_retries=0),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
            "meta": {"count": 1},
            "results": [{"id": "https://openalex.org/W1", "display_name": "Trust across remote teams"}],
        })),
    )
    crossref_http = ConnectorHttpClient(
        "crossref", "https://api.crossref.org", HttpPolicy(max_retries=0),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "ok", "message": {"items": "not-a-list"}})),
    )
    connectors = {"openalex": OpenAlexConnector(openalex_http), "crossref": CrossrefConnector(crossref_http)}
    monkeypatch.setattr(idea_brief, "is_enabled", lambda name, enabled_list, allow: name in connectors)
    monkeypatch.setattr(idea_brief, "build_connector", lambda name: connectors[name])
    pid = create_project(client, "Landscape")

    response = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust"})

    assert response.status_code == 200
    body = response.json()
    services = {s["service"]: s for s in body["services"]}
    assert services["openalex"]["status"] == "ok" and services["openalex"]["count"] == 1
    assert services["crossref"]["status"] == "failed"
    assert services["crossref"]["error"]
    assert body["sample_size"] == 1
    assert body["top_cited_in_sample"] == []  # no cited_by_count reported: never read as zero


def test_no_enabled_connector_is_refused_with_a_conflict_without_a_search(client, monkeypatch):
    pid = create_project(client, "Landscape")
    monkeypatch.setattr(idea_brief, "is_enabled", lambda *args: False)
    called = []
    monkeypatch.setattr(idea_brief, "build_connector", lambda name: called.append(name))
    response = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust"})
    assert response.status_code == 409
    assert called == []


def test_blank_query_gives_an_empty_scan_without_a_search(client, enabled):
    pid = create_project(client, "Landscape")
    fake = enabled(FakeConnector([_paper("W1", "Trust", 2020, 3)]))
    body = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "   "}).json()
    assert body["services"] == []
    assert body["top_cited_in_sample"] == [] and body["sample_size"] == 0
    assert fake.requests == []


def test_the_review_cutoff_year_is_the_utc_year(client, enabled, monkeypatch):
    class FakeDatetime:
        @staticmethod
        def now(tz=None):
            return datetime(2031 if tz is not None else 2030, 6, 1)

    monkeypatch.setattr(idea_brief, "datetime", FakeDatetime)
    pid = create_project(client, "Landscape")
    enabled(FakeConnector([]))
    body = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust"}).json()
    assert body["reviews_since"] == 2031 - 5


def test_limit_above_the_cap_is_refused(client, enabled):
    pid = create_project(client, "Landscape")
    enabled(FakeConnector([]))
    response = client.get(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust", "limit": 26})
    assert response.status_code == 422


# --- stored scan (POST) -----------------------------------------------------------------------------------------------


def test_posting_a_blank_query_is_refused_and_nothing_is_stored(client, enabled):
    pid = create_project(client, "Landscape")
    enabled(FakeConnector([]))
    response = client.post(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "   "})
    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.scalars(select(LandscapeScanRun).where(LandscapeScanRun.project_id == uuid.UUID(pid))).first() is None


def test_posting_with_no_enabled_connector_is_refused_with_a_conflict(client, monkeypatch):
    pid = create_project(client, "Landscape")
    monkeypatch.setattr(idea_brief, "is_enabled", lambda *args: False)
    response = client.post(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust"})
    assert response.status_code == 409


def test_posting_a_scan_stores_an_artifact_with_per_service_counts_and_no_abstract_or_title_text(client, enabled_services):
    pid = create_project(client, "Landscape")
    secret_title = "An unpublished working title that must never leave the raw search response"
    openalex = FakeConnector([_paper("W1", secret_title, 2020, 5, connector="openalex")])
    crossref = FakeConnector([], error=ConnectorError("service down https://x.example/?api_key=SECRET123"))
    enabled_services({"openalex": openalex, "crossref": crossref})

    response = client.post(f"/api/projects/{pid}/idea-brief/landscape-scan", params={"query": "trust"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["query"] == "trust"
    assert body["sample_size"] == 1
    assert body["record_count"] == 1
    services = {s["service"]: s for s in body["services"]}
    assert services["openalex"] == {"service": "openalex", "status": "ok", "count": 1, "total_reported": None, "error": None}
    assert services["crossref"]["status"] == "failed"

    with SessionLocal() as db:
        run = db.get(LandscapeScanRun, uuid.UUID(body["id"]))
        assert run is not None
        assert run.project_id == uuid.UUID(pid)
        assert run.query == "trust"
        assert run.sample_size == 1
        assert run.record_ids == [{"connector": "openalex", "external_id": "W1"}]
        stored_text = json.dumps(run.services) + json.dumps(run.record_ids)
        assert secret_title not in stored_text  # no abstracts or titles, only ids and counts

        artifact = db.scalar(select(Artifact).where(Artifact.kind == "landscape_scan", Artifact.ref_id == str(run.id)))
        assert artifact is not None
        assert artifact.project_id == uuid.UUID(pid)
        assert artifact.stage.value == "idea"
