"""Open-access full-text fetch (plan M2.7.1): SSRF-guarded download + licence-checked storage."""

import hashlib
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import task_handlers, task_runner
from app.config import get_settings
from app.connectors.unpaywall import OaLocation
from app.database import SessionLocal
from app.main import app
from app.models import Source, Task, TaskStatus
from app.object_storage import LocalDiskStore
from app.oa_fetch import licence_permits_storage
from app.safe_fetch import FetchFailed, FetchRefused, check_url, fetch_pdf, is_public_address

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF\n"
DOI = "10.1234/oa.2021"
PUBLIC = "93.184.216.34"


def resolver(table=None):
    table = {"example.org": [PUBLIC], "cdn.example.org": [PUBLIC], **(table or {})}
    return lambda host, port: table.get(host, [PUBLIC])


def fetch(url, handler, table=None, max_bytes=1000):
    return fetch_pdf(url, max_bytes=max_bytes, timeout_seconds=5, resolver=resolver(table), transport=httpx.MockTransport(handler))


# --- the URL guard -------------------------------------------------------------------------------

@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                                     "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "224.0.0.1", "nonsense"])
def test_private_and_reserved_addresses_are_not_public(address):
    assert not is_public_address(address)


def test_a_public_address_is_public():
    assert is_public_address(PUBLIC) and is_public_address("2606:4700:4700::1111")


@pytest.mark.parametrize("url", ["ftp://example.org/a.pdf", "file:///etc/passwd", "https://user:pw@example.org/a.pdf",
                                 "https://example.org:8443/a.pdf", "http://example.org:22/a.pdf", "https:///a.pdf", "https://[::1/a.pdf"])
def test_bad_urls_are_refused(url):
    with pytest.raises(FetchRefused):
        check_url(url, resolver())


def test_a_host_resolving_to_any_private_address_is_refused():
    with pytest.raises(FetchRefused, match="private"):
        check_url("https://evil.example/a.pdf", resolver({"evil.example": [PUBLIC, "10.0.0.1"]}))


def test_an_ip_literal_url_is_checked_too():
    with pytest.raises(FetchRefused):
        check_url("http://169.254.169.254/latest/meta-data", lambda h, p: [h])


def test_explicit_default_ports_are_fine():
    check_url("https://example.org:443/a.pdf", resolver())
    check_url("http://example.org:80/a.pdf", resolver())


# --- the download --------------------------------------------------------------------------------

def test_a_pdf_is_downloaded():
    data, final = fetch("https://example.org/a.pdf", lambda r: httpx.Response(200, content=PDF))
    assert data == PDF and final == "https://example.org/a.pdf"


def test_redirects_are_followed_and_each_hop_is_checked():
    def handler(request):
        if request.url.host == "example.org":
            return httpx.Response(302, headers={"location": "https://cdn.example.org/files/a.pdf"})
        return httpx.Response(200, content=PDF)

    data, final = fetch("https://example.org/a.pdf", handler)
    assert data == PDF and final == "https://cdn.example.org/files/a.pdf"


def test_a_relative_redirect_is_resolved_against_the_current_url():
    def handler(request):
        if request.url.path == "/a.pdf":
            return httpx.Response(301, headers={"location": "/real/a.pdf"})
        return httpx.Response(200, content=PDF)

    assert fetch("https://example.org/a.pdf", handler)[1] == "https://example.org/real/a.pdf"


def test_a_redirect_into_the_internal_network_is_refused_before_it_is_requested():
    asked = []

    def handler(request):
        asked.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://metadata.internal/latest"})

    with pytest.raises(FetchRefused, match="private"):
        fetch("https://example.org/a.pdf", handler, {"metadata.internal": ["169.254.169.254"]})
    assert asked == ["example.org"]


def test_endless_redirects_are_refused():
    with pytest.raises(FetchRefused, match="redirected"):
        fetch("https://example.org/a.pdf", lambda r: httpx.Response(302, headers={"location": "https://example.org/again"}))


def test_an_html_page_is_not_a_pdf():
    with pytest.raises(FetchRefused, match="did not return a PDF"):
        fetch("https://example.org/a.pdf", lambda r: httpx.Response(200, content=b"<html>Sign in</html>", headers={"content-type": "application/pdf"}))


def test_a_declared_oversize_body_is_refused():
    with pytest.raises(FetchRefused, match="larger"):
        fetch("https://example.org/a.pdf", lambda r: httpx.Response(200, content=PDF, headers={"content-length": "5000"}))


def test_an_undeclared_oversize_body_is_cut_off():
    def handler(request):
        return httpx.Response(200, stream=httpx.ByteStream(PDF + b"x" * 5000))

    with pytest.raises(FetchRefused, match="larger"):
        fetch("https://example.org/a.pdf", handler)


@pytest.mark.parametrize("code,error", [(404, FetchRefused), (403, FetchRefused), (429, FetchFailed), (503, FetchFailed)])
def test_http_errors(code, error):
    with pytest.raises(error):
        fetch("https://example.org/a.pdf", lambda r: httpx.Response(code))


def test_a_connection_failure_can_be_retried():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(FetchFailed):
        fetch("https://example.org/a.pdf", handler)


# --- the licence rule ----------------------------------------------------------------------------

@pytest.mark.parametrize("licence,ok", [("cc-by", True), ("CC-BY-NC-ND", True), ("cc0", True), ("pd", True), (None, False), ("", False),
                                        ("implied-oa", False), ("publisher-specific-oa", False), ("other-oa", False), ("cc-by-x", False)])
def test_only_listed_licences_permit_storage(licence, ok):
    assert licence_permits_storage(licence, get_settings().fulltext_store_licences) is ok


# --- end to end through the API and the task queue -----------------------------------------------

class FakeUnpaywall:
    def __init__(self):
        self.location = OaLocation(doi=DOI, pdf_url="https://example.org/a.pdf", landing_page_url="https://example.org/a", license="cc-by",
                                   version="publishedVersion", host_type="publisher")
        self.asked = []

    def get_oa_location(self, doi):
        self.asked.append(doi)
        return self.location


@pytest.fixture
def oa(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "connectors_enabled", ["unpaywall"])
    monkeypatch.setattr(settings, "connector_contact_email", "lab@example.com")
    monkeypatch.setattr(settings, "object_storage_root", str(tmp_path / "objects"))
    state = type("S", (), {"unpaywall": FakeUnpaywall(), "pdf": PDF, "error": None, "fetched": []})()

    def fake_fetch(url, **kwargs):
        state.fetched.append(url)
        if state.error:
            raise state.error
        return state.pdf, url

    monkeypatch.setattr(task_handlers, "build_unpaywall", lambda: state.unpaywall)
    monkeypatch.setattr(task_handlers, "fetch_pdf", fake_fetch)
    state.store = LocalDiskStore(tmp_path / "objects", settings.object_storage_max_bytes)
    return state


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    c = TestClient(app)
    c.post("/api/auth/development/login", json={"email": f"oa-{tag}@example.com", "display_name": "O"})
    pid = c.post("/api/projects", json={"title": "OA"}).json()["id"]
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


def audit_of(c, pid, action):
    return [e for e in c.get(f"/api/projects/{pid}/audit").json() if e["action"] == action]


def test_a_cc_by_pdf_is_stored_write_once_with_its_licence(world, oa):
    c, pid, sid = world
    source, task = run(c, pid, sid)
    assert task.status == TaskStatus.completed
    access = source["fulltext_access"]
    assert access["status"] == "stored" and access["licence"] == "cc-by" and access["version"] == "publishedVersion"
    assert access["sha256"] == hashlib.sha256(PDF).hexdigest() and access["size"] == len(PDF)
    assert source["fulltext_path"] == f"projects/{pid}/sources/{sid}/fulltext.pdf"
    assert oa.store.get(source["fulltext_path"]) == PDF
    assert source["oa_url"] == "https://example.org/a.pdf"
    [event] = audit_of(c, pid, "source.fulltext_checked")
    assert event["payload_json"]["status"] == "stored" and event["payload_json"]["licence"] == "cc-by"
    assert audit_of(c, pid, "source.fulltext_requested")


@pytest.mark.parametrize("licence", [None, "implied-oa", "publisher-specific-oa"])
def test_an_unknown_or_unlisted_licence_keeps_the_link_only(world, oa, licence):
    c, pid, sid = world
    oa.unpaywall.location = oa.unpaywall.location.model_copy(update={"license": licence})
    source, task = run(c, pid, sid)
    assert task.status == TaskStatus.completed
    assert source["fulltext_access"]["status"] == "link_only" and source["fulltext_access"]["licence"] == licence
    assert source["fulltext_path"] is None and source["oa_url"] == "https://example.org/a.pdf"
    assert oa.fetched == []  # never downloaded


def test_no_open_access_copy_is_recorded(world, oa):
    c, pid, sid = world
    oa.unpaywall.location = None
    source, _ = run(c, pid, sid)
    assert source["fulltext_access"]["status"] == "no_oa" and source["fulltext_path"] is None and oa.fetched == []


def test_a_permitted_licence_without_a_pdf_link_keeps_the_landing_page(world, oa):
    c, pid, sid = world
    oa.unpaywall.location = oa.unpaywall.location.model_copy(update={"pdf_url": None})
    source, _ = run(c, pid, sid)
    assert source["fulltext_access"]["status"] == "no_pdf" and source["oa_url"] == "https://example.org/a" and oa.fetched == []


def test_a_refused_download_fails_the_task_loudly_and_records_nothing(world, oa):
    c, pid, sid = world
    oa.error = FetchRefused("The PDF link did not return a PDF")
    source, task = run(c, pid, sid)
    assert task.status == TaskStatus.failed and "did not return a PDF" in task.error
    assert source["fulltext_access"] is None and source["fulltext_path"] is None
    assert not audit_of(c, pid, "source.fulltext_checked")


def test_a_different_file_never_replaces_a_stored_one(world, oa):
    c, pid, sid = world
    oa.store.put(f"projects/{pid}/sources/{sid}/fulltext.pdf", PDF + b"older copy")
    source, task = run(c, pid, sid)
    assert task.status == TaskStatus.failed and "not replaced" in task.error
    assert source["fulltext_path"] is None


def test_requests_are_validated(world, oa, monkeypatch):
    c, pid, sid = world
    no_doi = c.post(f"/api/projects/{pid}/sources", json={"title": "No DOI"}).json()["id"]
    assert c.post(f"/api/projects/{pid}/sources/{no_doi}/fulltext/fetch").status_code == 400
    assert c.post(f"/api/projects/{pid}/sources/{uuid.uuid4()}/fulltext/fetch").status_code == 404

    assert c.post(f"/api/projects/{pid}/sources/{sid}/fulltext/fetch").status_code == 202
    assert c.post(f"/api/projects/{pid}/sources/{sid}/fulltext/fetch").status_code == 409  # one already waiting
    while task_runner.run_one_task():
        pass
    assert c.post(f"/api/projects/{pid}/sources/{sid}/fulltext/fetch").status_code == 409  # already stored

    monkeypatch.setattr(get_settings(), "connector_contact_email", None)
    r = c.post(f"/api/projects/{pid}/sources/{no_doi}/fulltext/fetch")
    assert r.status_code == 400
    other = c.post(f"/api/projects/{pid}/sources", json={"title": "Other", "doi": "10.1234/other"}).json()["id"]
    assert "CONNECTOR_CONTACT_EMAIL" in c.post(f"/api/projects/{pid}/sources/{other}/fulltext/fetch").json()["detail"]
    monkeypatch.setattr(get_settings(), "connectors_enabled", [])
    assert "not enabled" in c.post(f"/api/projects/{pid}/sources/{other}/fulltext/fetch").json()["detail"]


def test_a_person_cannot_set_the_fetch_outcome(world):
    c, pid, _ = world
    r = c.post(f"/api/projects/{pid}/sources", json={"title": "Sneaky", "fulltext_access": {"status": "stored"}, "fulltext_path": "x.pdf"})
    assert r.status_code in (201, 422)
    if r.status_code == 201:
        with SessionLocal() as db:
            source = db.scalar(select(Source).where(Source.id == uuid.UUID(r.json()["id"])))
            assert source.fulltext_access is None and source.fulltext_path is None
