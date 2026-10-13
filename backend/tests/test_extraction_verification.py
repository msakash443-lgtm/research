import uuid
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Extraction, Source
from gate_helpers import approve_earlier_gates
from tests.test_fulltext import make_pdf


def _setup(monkeypatch):
    owner = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    owner.post("/api/auth/development/login", json={"email": f"verify-{tag}@example.com", "display_name": "V"})
    project = owner.post("/api/projects", json={"title": "Verify"}).json()["id"]
    source = owner.post(f"/api/projects/{project}/sources", json={"title": "Paper"}).json()["id"]
    pdf = make_pdf([["Methods", "We studied 42 people and found a small effect."]])
    from app.routers import extractions as routes
    monkeypatch.setattr(routes, "get_object_store", lambda settings: type("Store", (), {"get": lambda self, key: pdf})())
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(source)).fulltext_path = "stored-pdf"
        db.commit()
    return owner, project, source, pdf


def _extraction(project, source):
    extraction_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Extraction(id=extraction_id, project_id=uuid.UUID(project), source_id=uuid.UUID(source),
                          schema_version="default@1", fields_json={"sample": {"n": 42, "population": "People", "country": "UK"}},
                          evidence_spans={"sample": {"quote": "We studied 42 people", "page": 1}},
                          extracted_by="ai", extractor="agent:test", model_id="test-model", prompt_version="test@1"))
        db.commit()
    return str(extraction_id)


def test_human_verification_rechecks_pdf_and_g4_waits_for_it(monkeypatch):
    owner, project, source, pdf = _setup(monkeypatch)
    extraction_id = _extraction(project, source)
    approve_earlier_gates(project, "G4")
    base = f"/api/projects/{project}"
    queue = owner.get(base + "/extractions/verification-queue").json()
    assert queue[0]["critical_fields"] == ["measures", "sample", "key_findings"]
    assert queue[0]["verified_by_human"] is False
    pdf_response = owner.get(base + f"/extractions/{extraction_id}/pdf")
    assert pdf_response.content == pdf
    assert pdf_response.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in pdf_response.headers["content-security-policy"]
    assert owner.post(base + "/gates/G4/approve").status_code == 409
    assert owner.post(base + f"/extractions/{extraction_id}/verify", json={"confirmed_critical_fields": ["sample"]}).status_code == 422
    body = {"confirmed_critical_fields": ["measures", "sample", "key_findings"]}
    result = owner.post(base + f"/extractions/{extraction_id}/verify", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["verified_by_human"] is True and result.json()["verified_by"]
    assert owner.post(base + "/gates/G4/approve").status_code == 200


def test_verification_refuses_mismatched_pdf_quote_and_unextracted_source_blocks_g4(monkeypatch):
    owner, project, source, _ = _setup(monkeypatch)
    extraction_id = _extraction(project, source)
    body = {"confirmed_critical_fields": ["measures", "sample", "key_findings"]}
    with SessionLocal() as db:
        row = db.get(Extraction, uuid.UUID(extraction_id))
        row.evidence_spans = {"sample": {"quote": "a fabricated quote", "page": 1}}
        db.commit()
    assert owner.post(f"/api/projects/{project}/extractions/{extraction_id}/verify", json=body).status_code == 422
    approve_earlier_gates(project, "G4")
    assert owner.post(f"/api/projects/{project}/gates/G4/approve").status_code == 409


def test_a_newer_unverified_extraction_supersedes_the_verified_one(monkeypatch):
    owner, project, source, _ = _setup(monkeypatch)
    old_id = _extraction(project, source)
    with SessionLocal() as db:
        old = db.get(Extraction, uuid.UUID(old_id))
        old.verified_by_human, old.verified_by, old.verified_at = True, "tester", old.created_at
        old.updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        db.commit()
    newer_id = _extraction(project, source)
    queue = owner.get(f"/api/projects/{project}/extractions/verification-queue").json()
    assert queue[0]["extraction_id"] == newer_id
    assert queue[0]["verified_by_human"] is False
    approve_earlier_gates(project, "G4")
    assert owner.post(f"/api/projects/{project}/gates/G4/approve").status_code == 409


def test_approved_g4_rejects_new_sources_until_reentry():
    owner = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    owner.post("/api/auth/development/login", json={"email": f"late-{tag}@example.com", "display_name": "V"})
    project = owner.post("/api/projects", json={"title": "Locked corpus"}).json()["id"]
    approve_earlier_gates(project, "G4")
    assert owner.post(f"/api/projects/{project}/gates/G4/approve").status_code == 200
    result = owner.post(f"/api/projects/{project}/sources", json={"title": "Too late"})
    assert result.status_code == 409 and "G4" in result.json()["detail"]
    imported = owner.post(f"/api/projects/{project}/sources/import", json={"format": "bibtex", "text": "@article{x, title={Too late}}"})
    assert imported.status_code == 409


def test_source_guard_is_registered_when_only_models_are_imported_in_a_worker_process():
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", "from app.models import Source; print(len(list(Source.__mapper__.dispatch.before_insert)))"],
        cwd=backend, capture_output=True, text=True, check=True,
    )
    assert int(result.stdout.strip()) >= 1
