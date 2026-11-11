"""Licence-gated arXiv full-text retrieval (plan M1.4.8)."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app import arxiv_fulltext_task, task_runner
from app.config import get_settings
from app.connectors.arxiv import ArxivLicense
from app.database import SessionLocal
from app.main import app
from app.models import SOURCE_ORIGIN_RETRIEVED, Source, Task, TaskStatus
from app.object_storage import LocalDiskStore
from app.arxiv_fulltext import _allowlist_name

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF\n"
ARXIV_ID = "2101.00001"


class FakeArxiv:
    def __init__(self, record):
        self.record = record
        self.looked_up = []

    def get_license(self, external_id):
        self.looked_up.append(external_id)
        return self.record


@pytest.mark.parametrize(
    "uri,name",
    [
        ("http://creativecommons.org/licenses/by/3.0/", "cc-by"),
        ("https://creativecommons.org/licenses/by-sa/4.0/", "cc-by-sa"),
        ("https://creativecommons.org/licenses/by-nc/4.0/", "cc-by-nc"),
        ("https://creativecommons.org/licenses/by-nc-sa/4.0/", "cc-by-nc-sa"),
        ("https://creativecommons.org/licenses/by-nd/4.0/", "cc-by-nd"),
        ("https://creativecommons.org/licenses/by-nc-nd/4.0/", "cc-by-nc-nd"),
        ("https://creativecommons.org/publicdomain/zero/1.0/", "cc0"),
        ("https://arxiv.org/licenses/nonexclusive-distrib/1.0/", None),
    ],
)
def test_only_recognised_creative_commons_licenses_map_to_storage_allowlist_names(uri, name):
    assert _allowlist_name(uri) == name


@pytest.fixture
def arxiv(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "connectors_enabled", ["arxiv"])
    monkeypatch.setattr(settings, "object_storage_root", str(tmp_path / "objects"))
    monkeypatch.setattr(settings, "fulltext_store_licences", ["cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nc-sa", "cc-by-nd", "cc-by-nc-nd", "cc0"])
    state = type("State", (), {"record": ArxivLicense("https://creativecommons.org/licenses/by/4.0/", "v3"),
                               "fetched": [], "error": None})()
    state.connector = FakeArxiv(state.record)

    def fake_fetch(url, **kwargs):
        state.fetched.append((url, kwargs))
        if state.error:
            raise state.error
        return PDF, url

    monkeypatch.setattr(arxiv_fulltext_task, "ArxivConnector", lambda: state.connector)
    monkeypatch.setattr(arxiv_fulltext_task, "fetch_pdf", fake_fetch)
    state.store = LocalDiskStore(tmp_path / "objects", settings.object_storage_max_bytes)
    return state


@pytest.fixture
def arxiv_world():
    tag = uuid.uuid4().hex[:8]
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"arxiv-{tag}@example.com", "display_name": "A"})
    project_id = client.post("/api/projects", json={"title": "arXiv"}).json()["id"]
    source_id = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "An arXiv paper", "doi": "10.1234/arxiv.2021", "oa_url": f"https://arxiv.org/pdf/{ARXIV_ID}"},
    ).json()["id"]
    with SessionLocal() as db:
        source = db.get(Source, uuid.UUID(source_id))
        source.origin = SOURCE_ORIGIN_RETRIEVED
        source.source_ids = {"arxiv": ARXIV_ID}
        db.commit()
    return client, project_id, source_id


def run_fetch(world):
    client, project_id, source_id = world
    response = client.post(f"/api/projects/{project_id}/sources/{source_id}/fulltext/arxiv/fetch")
    assert response.status_code == 202, response.text
    while task_runner.run_one_task():
        pass
    source = next(row for row in client.get(f"/api/projects/{project_id}/sources").json() if row["id"] == source_id)
    with SessionLocal() as db:
        task = db.get(Task, uuid.UUID(response.json()["task_id"]))
    return source, task


def test_a_permitted_cc_license_fetches_and_stores_the_exact_current_version(arxiv, arxiv_world):
    source, task = run_fetch(arxiv_world)
    project_id, source_id = arxiv_world[1:]
    assert task.status == TaskStatus.completed
    assert arxiv.connector.looked_up == [ARXIV_ID]
    assert arxiv.fetched[0][0] == f"https://arxiv.org/pdf/{ARXIV_ID}v3"
    assert arxiv.fetched[0][1]["max_bytes"] == get_settings().object_storage_max_bytes
    assert source["fulltext_access"]["status"] == "stored"
    assert source["fulltext_access"]["licence"] == "cc-by"
    assert source["fulltext_access"]["license_uri"] == "https://creativecommons.org/licenses/by/4.0/"
    assert source["fulltext_access"]["version"] == "v3"
    assert source["fulltext_path"] == f"projects/{project_id}/sources/{source_id}/fulltext.pdf"
    assert arxiv.store.get(source["fulltext_path"]) == PDF
    assert source["oa_url"] == f"https://arxiv.org/pdf/{ARXIV_ID}v3"
    events = client_audit(arxiv_world, "source.fulltext_checked")
    assert len(events) == 1 and events[0]["payload_json"]["via"] == "arxiv"


@pytest.mark.parametrize(
    "license_uri",
    [
        "http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
        "https://creativecommons.org/licenses/by/5.0/",
        None,
    ],
)
def test_unknown_or_unrecognised_licenses_are_link_only_and_never_downloaded(arxiv, arxiv_world, license_uri):
    arxiv.connector.record = ArxivLicense(license_uri, "v2")
    source, task = run_fetch(arxiv_world)
    assert task.status == TaskStatus.completed
    assert arxiv.fetched == []
    assert source["fulltext_access"]["status"] == "link_only"
    assert source["fulltext_access"]["license_uri"] == license_uri
    assert source["fulltext_path"] is None
    assert source["oa_url"] == f"https://arxiv.org/pdf/{ARXIV_ID}v2"


def test_an_missing_oai_record_is_link_only(arxiv, arxiv_world):
    arxiv.connector.record = None
    source, task = run_fetch(arxiv_world)
    assert task.status == TaskStatus.completed
    assert arxiv.fetched == []
    assert source["fulltext_access"]["status"] == "link_only"
    assert source["fulltext_access"]["version"] is None


def test_a_download_failure_fails_loudly_without_persisting_an_outcome(arxiv, arxiv_world):
    from app.safe_fetch import FetchRefused

    arxiv.error = FetchRefused("PDF signature was missing")
    source, task = run_fetch(arxiv_world)
    assert task.status == TaskStatus.failed
    assert "PDF signature was missing" in task.error
    assert source["fulltext_access"] is None and source["fulltext_path"] is None


def test_request_requires_retrieved_arxiv_metadata_and_prevents_parallel_fetches(arxiv, arxiv_world, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "connectors_enabled", ["arxiv", "unpaywall"])
    monkeypatch.setattr(settings, "connector_contact_email", "lab@example.com")
    client, project_id, source_id = arxiv_world
    route = f"/api/projects/{project_id}/sources/{source_id}/fulltext/arxiv/fetch"
    assert client.post(route).status_code == 202
    assert client.post(route).status_code == 409
    assert client.post(f"/api/projects/{project_id}/sources/{source_id}/fulltext/fetch").status_code == 409

    other = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "Another retrieved source", "doi": "10.1234/arxiv.other"},
    ).json()["id"]
    with SessionLocal() as db:
        source = db.get(Source, uuid.UUID(other))
        source.origin = SOURCE_ORIGIN_RETRIEVED
        source.source_ids = {"arxiv": "2101.00002"}
        db.commit()
    assert client.post(f"/api/projects/{project_id}/sources/{other}/fulltext/fetch").status_code == 202
    assert client.post(f"/api/projects/{project_id}/sources/{other}/fulltext/arxiv/fetch").status_code == 409

    manual = client.post(f"/api/projects/{project_id}/sources", json={"title": "Manual source"}).json()["id"]
    assert client.post(f"/api/projects/{project_id}/sources/{manual}/fulltext/arxiv/fetch").status_code == 400
    assert client.post(f"/api/projects/{project_id}/sources/{uuid.uuid4()}/fulltext/arxiv/fetch").status_code == 404


def client_audit(world, action):
    client, project_id, _ = world
    return [event for event in client.get(f"/api/projects/{project_id}/audit").json() if event["action"] == action]
