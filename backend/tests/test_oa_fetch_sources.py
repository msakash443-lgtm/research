"""Open-access location sources after Unpaywall (plan M2.7.6): OpenAlex, then CORE. Offline only."""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import task_handlers, task_runner
from app.config import get_settings
from app.connectors.base import ConnectorError
from app.connectors.core import CoreConnector
from app.connectors.http import ConnectorHttpClient, HttpPolicy
from app.connectors.openalex import OpenAlexConnector, to_oa_location
from app.connectors.unpaywall import OaLocation
from app.database import SessionLocal
from app.main import app
from app.models import Task, TaskStatus
from app.object_storage import LocalDiskStore
from app.safe_fetch import FetchRefused

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF\n"
DOI = "10.1234/oa.2021"  # lower case: CORE matches the exact DOI
OPENALEX_BASE = "https://api.openalex.org"
CORE_BASE = "https://api.core.ac.uk/v3"
OPENALEX_PDF = "https://repo.example.org/oa.pdf"
UNPAYWALL_PDF = "https://example.org/a.pdf"


def openalex_work(pdf=OPENALEX_PDF, license="cc-by"):
    best = {
        "landing_page_url": "https://repo.example.org/oa", "pdf_url": pdf, "license": license,
        "version": "acceptedVersion", "source": {"host_type": "repository"},
    }
    return {"best_oa_location": best}


def core_result(doi=DOI, license=None):
    work = {
        "id": 7, "title": "Open paper", "doi": f"https://doi.org/{doi}", "downloadUrl": "https://core.ac.uk/download/7.pdf",
        "links": [{"type": "display", "url": "https://core.ac.uk/works/7"}],
    }
    if license is not None:
        work["license"] = license
    return work


class Upstream:
    """Scripted Unpaywall, OpenAlex, CORE and PDF download. Records which APIs were asked."""

    def __init__(self):
        self.unpaywall = OaLocation(
            doi=DOI, pdf_url=UNPAYWALL_PDF, landing_page_url="https://example.org/a", license="cc-by",
            version="publishedVersion", host_type="publisher",
        )
        self.openalex = None  # the work for /works/doi:..., None answers 404 (no record)
        self.openalex_status = 200
        self.core_results = []
        self.core_status = 200
        self.hosts = []  # the API hosts asked, in order
        self.fetched = []
        self.fetch_error = None

    def handler(self, request):
        self.hosts.append(request.url.host)
        if request.url.host == "api.openalex.org":
            if self.openalex_status != 200:
                return httpx.Response(self.openalex_status)
            if self.openalex is None:
                return httpx.Response(404)
            return httpx.Response(200, json=self.openalex)
        if self.core_status != 200:
            return httpx.Response(self.core_status)
        return httpx.Response(200, json={"results": self.core_results})


class FakeUnpaywall:
    def __init__(self, upstream):
        self.upstream = upstream

    def get_oa_location(self, doi):
        return self.upstream.unpaywall


@pytest.fixture
def up(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "connectors_enabled", ["unpaywall", "openalex", "core"])
    monkeypatch.setattr(settings, "core_api_key", SecretStr("core-test-key"))
    monkeypatch.setattr(settings, "connector_contact_email", "lab@example.com")
    monkeypatch.setattr(settings, "object_storage_root", str(tmp_path / "objects"))
    upstream = Upstream()

    def http(name, base):
        return ConnectorHttpClient(
            name, base, HttpPolicy(max_retries=0, min_interval_seconds=0),
            transport=httpx.MockTransport(upstream.handler), sleep=lambda seconds: None,
        )

    def fake_fetch(url, **kwargs):
        upstream.fetched.append(url)
        if upstream.fetch_error:
            raise upstream.fetch_error
        return PDF, url

    monkeypatch.setattr(task_handlers, "build_unpaywall", lambda: FakeUnpaywall(upstream))
    monkeypatch.setattr(task_handlers, "build_openalex_oa", lambda: OpenAlexConnector(http=http("openalex", OPENALEX_BASE)))
    monkeypatch.setattr(task_handlers, "build_core_oa", lambda: CoreConnector(http=http("core", CORE_BASE)))
    monkeypatch.setattr(task_handlers, "fetch_pdf", fake_fetch)
    upstream.store = LocalDiskStore(tmp_path / "objects", settings.object_storage_max_bytes)
    return upstream


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    c = TestClient(app)
    c.post("/api/auth/development/login", json={"email": f"oa-src-{tag}@example.com", "display_name": "O"})
    pid = c.post("/api/projects", json={"title": "OA sources"}).json()["id"]
    sid = c.post(f"/api/projects/{pid}/sources", json={"title": "An open paper", "doi": DOI}).json()["id"]
    return c, pid, sid


def run(c, pid, sid):
    r = c.post(f"/api/projects/{pid}/sources/{sid}/fulltext/fetch")
    assert r.status_code == 202, r.text
    while task_runner.run_one_task():
        pass
    source = next(s for s in c.get(f"/api/projects/{pid}/sources").json() if s["id"] == sid)
    with SessionLocal() as db:
        task = db.get(Task, uuid.UUID(r.json()["task_id"]))
    return source, task


# --- the chain, end to end through the API and the task queue ------------------------------------

def test_an_unpaywall_copy_wins_and_later_sources_are_not_asked(world, up):
    c, pid, sid = world
    up.openalex = openalex_work()
    up.core_results = [core_result(license="cc-by")]
    source, task = run(c, pid, sid)
    assert task.status == TaskStatus.completed
    assert source["fulltext_access"]["via"] == "unpaywall" and source["fulltext_access"]["status"] == "stored"
    assert up.hosts == [] and up.fetched == [UNPAYWALL_PDF]
    assert source["fulltext_access"]["sources"] == [
        {"source": "unpaywall", "outcome": "storable"},
        {"source": "openalex", "outcome": "not_consulted", "reason": "not_needed"},
        {"source": "core", "outcome": "not_consulted", "reason": "not_needed"},
    ]


def test_an_openalex_copy_is_stored_when_unpaywall_has_none(world, up):
    c, pid, sid = world
    up.unpaywall = None
    up.openalex = openalex_work()
    source, task = run(c, pid, sid)
    assert task.status == TaskStatus.completed
    access = source["fulltext_access"]
    assert access["status"] == "stored" and access["via"] == "openalex" and access["licence"] == "cc-by"
    assert access["version"] == "acceptedVersion" and access["host_type"] == "repository"
    assert up.fetched == [OPENALEX_PDF]
    assert source["fulltext_path"] == f"projects/{pid}/sources/{sid}/fulltext.pdf"
    assert up.store.get(source["fulltext_path"]) == PDF
    assert access["sources"] == [
        {"source": "unpaywall", "outcome": "no_oa"},
        {"source": "openalex", "outcome": "storable"},
        {"source": "core", "outcome": "not_consulted", "reason": "not_needed"},
    ]


def test_an_openalex_copy_with_an_unknown_licence_is_link_only_and_never_downloaded(world, up):
    c, pid, sid = world
    up.unpaywall = None
    up.openalex = openalex_work(license=None)
    source, task = run(c, pid, sid)
    assert task.status == TaskStatus.completed
    access = source["fulltext_access"]
    assert access["status"] == "link_only" and access["via"] == "openalex" and access["licence"] is None
    assert source["fulltext_path"] is None and source["oa_url"] == OPENALEX_PDF
    assert up.fetched == []


def test_a_core_copy_is_stored_when_openalex_has_none(world, up):
    c, pid, sid = world
    up.unpaywall = None
    up.core_results = [core_result(license="cc-by")]
    source, task = run(c, pid, sid)
    assert task.status == TaskStatus.completed
    access = source["fulltext_access"]
    assert access["status"] == "stored" and access["via"] == "core" and access["licence"] == "cc-by"
    assert up.fetched == ["https://core.ac.uk/download/7.pdf"]
    assert {"source": "openalex", "outcome": "no_oa"} in access["sources"]


def test_core_without_a_licence_is_link_only_because_its_licence_is_unknown(world, up):
    c, pid, sid = world
    up.unpaywall = None
    up.core_results = [core_result()]
    source, _ = run(c, pid, sid)
    assert source["fulltext_access"]["status"] == "link_only" and source["fulltext_access"]["via"] == "core"
    assert up.fetched == []


def test_core_is_not_asked_without_an_api_key(world, up, monkeypatch):
    c, pid, sid = world
    monkeypatch.setattr(get_settings(), "core_api_key", SecretStr(" "))
    up.unpaywall = None
    up.core_results = [core_result(license="cc-by")]
    source, _ = run(c, pid, sid)
    assert "api.core.ac.uk" not in up.hosts
    assert {"source": "core", "outcome": "not_consulted", "reason": "disabled"} in source["fulltext_access"]["sources"]
    assert source["fulltext_access"]["status"] == "no_oa"


def test_core_is_not_asked_unless_it_is_enabled(world, up, monkeypatch):
    c, pid, sid = world
    monkeypatch.setattr(get_settings(), "connectors_enabled", ["unpaywall", "openalex"])
    up.unpaywall = None
    up.core_results = [core_result(license="cc-by")]
    source, _ = run(c, pid, sid)
    assert "api.core.ac.uk" not in up.hosts
    assert {"source": "core", "outcome": "not_consulted", "reason": "disabled"} in source["fulltext_access"]["sources"]


def test_a_failing_lookup_is_recorded_by_class_name_and_the_next_source_is_still_asked(world, up):
    c, pid, sid = world
    up.unpaywall = None
    up.openalex_status = 500
    up.core_results = [core_result(license="cc-by")]
    source, task = run(c, pid, sid)
    assert task.status == TaskStatus.completed
    access = source["fulltext_access"]
    assert access["status"] == "stored" and access["via"] == "core"
    [openalex_entry] = [e for e in access["sources"] if e["source"] == "openalex"]
    assert openalex_entry["outcome"] == "error"
    assert openalex_entry["error"].endswith("Error")
    assert "core-test-key" not in str(access)


def test_when_every_lookup_fails_the_status_says_so(world, up):
    c, pid, sid = world
    up.unpaywall = None
    up.openalex_status = 500
    up.core_status = 500
    source, task = run(c, pid, sid)
    assert task.status == TaskStatus.completed
    assert source["fulltext_access"]["status"] == "lookup_failed" and source["fulltext_path"] is None
    assert up.fetched == []


def test_a_refused_openalex_download_fails_the_task_and_does_not_try_core(world, up):
    c, pid, sid = world
    up.unpaywall = None
    up.openalex = openalex_work()
    up.fetch_error = FetchRefused("The PDF link did not return a PDF")
    up.core_results = [core_result(license="cc-by")]
    source, task = run(c, pid, sid)
    assert task.status == TaskStatus.failed and "did not return a PDF" in task.error
    assert source["fulltext_access"] is None and source["fulltext_path"] is None
    assert "api.core.ac.uk" not in up.hosts


# --- the connectors' location lookups ------------------------------------------------------------

def test_openalex_reports_its_best_open_access_location():
    assert to_oa_location(openalex_work(), DOI) == OaLocation(
        doi=DOI, landing_page_url="https://repo.example.org/oa", pdf_url=OPENALEX_PDF, license="cc-by",
        version="acceptedVersion", host_type="repository",
    )


def test_openalex_without_a_url_has_no_location():
    assert to_oa_location({"best_oa_location": None}, DOI) is None
    assert to_oa_location({"best_oa_location": {"landing_page_url": None, "pdf_url": None}}, DOI) is None


def test_openalex_looks_up_the_doi_and_answers_none_for_no_record():
    up = Upstream()
    connector = OpenAlexConnector(http=ConnectorHttpClient(
        "openalex", OPENALEX_BASE, HttpPolicy(max_retries=0, min_interval_seconds=0),
        transport=httpx.MockTransport(up.handler), sleep=lambda seconds: None,
    ))
    assert connector.get_oa_location(DOI) is None  # 404
    up.openalex = openalex_work()
    assert connector.get_oa_location(DOI).pdf_url == OPENALEX_PDF


def core_connector(up):
    return CoreConnector(http=ConnectorHttpClient(
        "core", CORE_BASE, HttpPolicy(max_retries=0, min_interval_seconds=0),
        transport=httpx.MockTransport(up.handler), sleep=lambda seconds: None,
    ))


def test_core_only_matches_the_exact_doi():
    up = Upstream()
    up.core_results = [core_result(doi="10.1234/other.1", license="cc-by")]
    assert core_connector(up).get_oa_location(DOI) is None
    up.core_results = [core_result(license="cc-by")]
    location = core_connector(up).get_oa_location(DOI)
    assert location == OaLocation(doi=DOI, landing_page_url="https://core.ac.uk/works/7",
                                  pdf_url="https://core.ac.uk/download/7.pdf", license="cc-by")


def test_core_without_a_download_url_has_no_location():
    up = Upstream()
    result = core_result()
    del result["downloadUrl"]
    up.core_results = [result]
    assert core_connector(up).get_oa_location(DOI) is None


def test_core_rejects_an_unexpected_reply():
    up = Upstream()
    up.core_results = "not a list"
    with pytest.raises(ConnectorError):
        core_connector(up).get_oa_location(DOI)
