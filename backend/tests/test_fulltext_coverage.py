"""Full-text coverage indicator (X.76): full text / abstract only / not accessible, on every place a reviewer decides."""

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.fulltext_coverage import ABSTRACT_ONLY, FULL_TEXT, NOT_ACCESSIBLE, coverage
from app.main import app
from app.models import Source


def _src(**fields):
    base = dict(fulltext_path=None, abstract=None, oa_url=None, fulltext_access=None)
    return SimpleNamespace(**{**base, **fields})


def test_levels_follow_what_the_project_holds():
    assert coverage(_src(fulltext_path="k", abstract="A"))["level"] == FULL_TEXT  # a stored PDF beats an abstract
    assert coverage(_src(abstract="An abstract."))["level"] == ABSTRACT_ONLY
    assert coverage(_src(abstract="   "))["level"] == NOT_ACCESSIBLE  # whitespace is not an abstract
    assert coverage(_src())["level"] == NOT_ACCESSIBLE


def test_the_open_access_link_is_plain_http_only_and_the_reason_comes_from_the_lookup():
    assert coverage(_src(oa_url="https://repo.example/p.pdf"))["open_access_url"] == "https://repo.example/p.pdf"
    assert coverage(_src(oa_url="javascript:alert(1)"))["open_access_url"] is None
    assert coverage(_src(oa_url="ftp://x/y"))["open_access_url"] is None
    missing = coverage(_src(abstract="A", fulltext_access={"status": "link_only"}))
    assert missing["reason"] == "an open-access copy exists, but its licence does not allow storing it"
    assert coverage(_src(fulltext_path="k", fulltext_access={"status": "link_only"}))["reason"] is None


def _world():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"cov-{uuid.uuid4().hex[:8]}@example.com", "display_name": "C"})
    project = client.post("/api/projects", json={"title": "Coverage"}).json()["id"]
    return client, project


def _source(client, project, title, **fields):
    source_id = client.post(f"/api/projects/{project}/sources", json={"title": title}).json()["id"]
    with SessionLocal() as db:
        row = db.get(Source, uuid.UUID(source_id))
        for name, value in fields.items():
            setattr(row, name, value)
        db.commit()
    return source_id


def test_source_screening_queue_and_matrix_all_report_the_same_level():
    client, project = _world()
    full = _source(client, project, "Has the PDF", fulltext_path="stored", abstract="A")
    abstract = _source(client, project, "Abstract only", abstract="Some abstract.", oa_url="https://repo.example/a")
    none = _source(client, project, "Title only")

    sources = {s["id"]: s["coverage"] for s in client.get(f"/api/projects/{project}/sources").json()}
    assert {sid: c["level"] for sid, c in sources.items()} == {full: FULL_TEXT, abstract: ABSTRACT_ONLY, none: NOT_ACCESSIBLE}

    queue = client.get(f"/api/projects/{project}/screening/queue?stage=title_abstract").json()["items"]
    assert {i["source_id"]: i["coverage"]["level"] for i in queue} == {full: FULL_TEXT, abstract: ABSTRACT_ONLY, none: NOT_ACCESSIBLE}
    assert next(i for i in queue if i["source_id"] == abstract)["coverage"]["open_access_url"] == "https://repo.example/a"

    matrix = client.get(f"/api/projects/{project}/evidence-matrix").json()["papers"]
    assert {p["source_id"]: p["coverage"]["level"] for p in matrix} == {full: FULL_TEXT, abstract: ABSTRACT_ONLY, none: NOT_ACCESSIBLE}

    verification = client.get(f"/api/projects/{project}/extractions/verification-queue").json()
    assert {i["source_id"]: i["coverage"]["level"] for i in verification} == {full: FULL_TEXT, abstract: ABSTRACT_ONLY, none: NOT_ACCESSIBLE}
