"""X.42: a search run, a criteria save, a screening decision and an extraction are registered as artifacts,
so re-entering scoped marks each one stale (and editing an extraction makes it current again)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import artifacts as art
from app.config import get_settings
from app.connectors.base import ConnectorBase, PaperRecord, SearchPage, SearchRequest
from app.database import SessionLocal
from app.main import app
from app.models import Artifact, ArtifactStatus, Project, ProjectStage as S, User
from app.search_query import BooleanQuery, ConceptBlock
from app.search_runner import run_search
from gate_helpers import approve_earlier_gates


QUERY = BooleanQuery(blocks=(ConceptBlock(label="a", terms=("remote work",)),))
CRITERIA = {"framework": "custom", "criteria": [{"kind": "include", "text": "Empirical studies"}]}
EXTRACTION = {
    "fields": {"research_question": "Does remote work raise output?", "sample": {"n": 120, "population": "clerks", "country": "NL"}, "limitations": None},
    "evidence": {
        "research_question": {"quote": "We ask whether remote work raises output", "page": 2, "section": "Introduction"},
        "sample": {"quote": "120 clerks in the Netherlands", "page": None, "section": "Method"},
    },
}


class OneRecordConnector(ConnectorBase):
    name = "openalex"

    def search(self, request: SearchRequest) -> SearchPage:
        record = PaperRecord(connector="openalex", external_id="W1", title="A sufficiently long distinct title about work", doi=None, year=2020, authors=("Ada",))
        return SearchPage(records=(record,), total=1, next_cursor=None)


@pytest.fixture(autouse=True)
def enabled_connector(monkeypatch):
    monkeypatch.setattr(get_settings(), "connectors_enabled", ["openalex"])


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner = TestClient(app)
    owner.post("/api/auth/development/login", json={"email": f"x42-owner-{tag}@example.com", "display_name": "X"})
    pid = owner.post("/api/projects", json={"title": "X.42"}).json()["id"]
    approve_earlier_gates(pid, "G2")  # G1 first; gates go in order (M0.5.10)
    return {"owner": owner, "pid": pid}


def _artifacts(pid, kind):
    with SessionLocal() as db:
        return db.scalars(select(Artifact).where(Artifact.project_id == uuid.UUID(pid), Artifact.kind == kind)).all()


def _reenter_to_scoped(pid):
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(pid))
        project.stage = S.extracted
        art.reenter_stage(db, project, S.scoped, actor="tester", reason="X.42 test")
        db.commit()


def _statuses(pid, kind):
    rows = _artifacts(pid, kind)
    assert rows, f"no {kind} artifact was registered"
    return {row.status for row in rows}


def test_a_search_run_is_an_artifact_at_retrieved_and_goes_stale_on_re_entry(world):
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["pid"]))
        run_search(db, project=project, actor="u", connector=OneRecordConnector(), query=QUERY)
        db.commit()
    assert [(r.stage, r.status) for r in _artifacts(world["pid"], "search_run")] == [(S.retrieved, ArtifactStatus.current)]
    _reenter_to_scoped(world["pid"])
    assert _statuses(world["pid"], "search_run") == {ArtifactStatus.stale}


def test_a_criteria_save_is_an_artifact_at_search_planned_and_goes_stale_on_re_entry(world):
    assert world["owner"].put(f"/api/projects/{world['pid']}/criteria", json=CRITERIA).status_code == 200
    assert [(r.stage, r.status) for r in _artifacts(world["pid"], "criteria")] == [(S.search_planned, ArtifactStatus.current)]
    _reenter_to_scoped(world["pid"])
    assert _statuses(world["pid"], "criteria") == {ArtifactStatus.stale}


def test_a_screening_decision_is_an_artifact_at_screened_and_goes_stale_on_re_entry(world):
    source_id = world["owner"].post(f"/api/projects/{world['pid']}/sources", json={"title": "A paper"}).json()["id"]
    assert world["owner"].post(f"/api/projects/{world['pid']}/screening/decisions", json={"source_id": source_id, "decision": "include"}).status_code == 201
    assert [(r.stage, r.status) for r in _artifacts(world["pid"], "screening_decision")] == [(S.screened, ArtifactStatus.current)]
    _reenter_to_scoped(world["pid"])
    assert _statuses(world["pid"], "screening_decision") == {ArtifactStatus.stale}


def _source_with_pdf(world, monkeypatch):
    """A source whose stored PDF contains the quotes in EXTRACTION (extractions are span-checked against it)."""
    from app.models import Source
    from app.object_storage import fulltext_key
    from app.routers import extractions as extraction_routes
    from tests.test_fulltext import make_pdf

    sid = world["owner"].post(f"/api/projects/{world['pid']}/sources", json={"title": "A paper"}).json()["id"]
    pdf = make_pdf([["Other", "Unrelated page text"], [
        "Introduction", "We ask whether remote work raises output",
        "Method", "120 clerks in the Netherlands", "a randomised trial",
    ]])
    key = fulltext_key(uuid.UUID(world["pid"]), uuid.UUID(sid), "pdf")

    class Store:
        def get(self, requested_key):
            assert requested_key == key
            return pdf

    monkeypatch.setattr(extraction_routes, "get_object_store", lambda settings: Store())
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(sid)).fulltext_path = key
        db.commit()
    return sid


def test_an_extraction_is_an_artifact_at_extracted_and_goes_stale_on_re_entry(world, monkeypatch):
    source_id = _source_with_pdf(world, monkeypatch)
    created = world["owner"].post(f"/api/projects/{world['pid']}/extractions", json={"source_id": source_id, **EXTRACTION})
    assert created.status_code == 201
    assert [(r.ref_id, r.stage, r.status) for r in _artifacts(world["pid"], "extraction")] == [(created.json()["id"], S.extracted, ArtifactStatus.current)]
    _reenter_to_scoped(world["pid"])
    assert _statuses(world["pid"], "extraction") == {ArtifactStatus.stale}


def test_editing_a_stale_extraction_makes_its_artifact_current_again(world, monkeypatch):
    source_id = _source_with_pdf(world, monkeypatch)
    extraction_id = world["owner"].post(f"/api/projects/{world['pid']}/extractions", json={"source_id": source_id, **EXTRACTION}).json()["id"]
    _reenter_to_scoped(world["pid"])
    assert world["owner"].put(f"/api/projects/{world['pid']}/extractions/{extraction_id}", json=EXTRACTION).status_code == 200
    assert _statuses(world["pid"], "extraction") == {ArtifactStatus.current}
    assert len(_artifacts(world["pid"], "extraction")) == 1
