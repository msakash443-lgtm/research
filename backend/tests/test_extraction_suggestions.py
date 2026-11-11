import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import suggestion_store
from app.database import SessionLocal
from app.main import app
from app.models import Extraction, Gate, GateCode, GateStatus, Source
from gate_helpers import approve_earlier_gates


def _setup():
    owner = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    owner.post("/api/auth/development/login", json={"email": f"xsug-{tag}@example.com", "display_name": "X"})
    project = owner.post("/api/projects", json={"title": "Extraction suggestions"}).json()["id"]
    source = owner.post(f"/api/projects/{project}/sources", json={"title": "Paper"}).json()["id"]
    extraction_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Extraction(
            id=extraction_id, project_id=uuid.UUID(project), source_id=uuid.UUID(source), schema_version="default@1",
            fields_json={"sample": "old value"}, evidence_spans={"sample": {"quote": "old quote", "page": 1}},
            extracted_by="human", extractor="user:test", verified_by_human=True,
            verified_by="someone", verified_at=datetime.now(timezone.utc),
        ))
        db.commit()
    return owner, project, str(extraction_id)


def _suggest(project, extraction_id, content, evidence=None):
    with SessionLocal() as db:
        suggestion = suggestion_store.create_suggestion(
            db, project_id=uuid.UUID(project), target_kind="extraction", target_id=extraction_id,
            target_field="sample", content=content, model_id="model-x", prompt_version="evidence_extraction@1",
            producing_agent="extractor", confidence=0.9, evidence=evidence,
        )
        db.commit()
        return str(suggestion.id)


def _row(extraction_id):
    with SessionLocal() as db:
        row = db.get(Extraction, uuid.UUID(extraction_id))
        return dict(fields=dict(row.fields_json), evidence=dict(row.evidence_spans),
                    verified=row.verified_by_human, verified_by=row.verified_by)


def _url(project, path=""):
    return f"/api/projects/{project}/suggestions{path}"


def test_accepting_an_extraction_suggestion_writes_the_value_and_its_evidence_and_resets_verification():
    owner, project, extraction_id = _setup()
    span = {"quote": "new quote", "page": 2, "section": "Methods"}
    sid = _suggest(project, extraction_id, '{"n": 42}', evidence=[span])

    row = owner.post(_url(project, f"/{sid}/accept")).json()

    assert row["state"] == "accepted"
    assert _row(extraction_id) == dict(fields={"sample": {"n": 42}}, evidence={"sample": span}, verified=False, verified_by=None)


def test_edit_then_accept_writes_the_researchers_text_when_it_is_not_json():
    owner, project, extraction_id = _setup()
    sid = _suggest(project, extraction_id, '{"n": 42}', evidence=[{"quote": "q", "page": 1}])

    owner.post(_url(project, f"/{sid}/edit-accept"), json={"text": "My own wording"})

    assert _row(extraction_id)["fields"] == {"sample": "My own wording"}


def test_an_extraction_suggestion_is_refused_once_g4_is_approved_and_nothing_is_written():
    owner, project, extraction_id = _setup()
    approve_earlier_gates(project, "G4")
    with SessionLocal() as db:
        db.scalar(select(Gate).where(Gate.project_id == uuid.UUID(project), Gate.code == GateCode.G4)).status = GateStatus.approved
        db.commit()
    sid = _suggest(project, extraction_id, '{"n": 42}', evidence=[{"quote": "q", "page": 1}])
    before = _row(extraction_id)

    response = owner.post(_url(project, f"/{sid}/accept"))

    assert response.status_code == 422 and "G4 is approved" in response.json()["detail"]
    assert _row(extraction_id) == before


def test_undo_of_an_extraction_suggestion_is_refused_with_the_no_field_history_message():
    owner, project, extraction_id = _setup()
    sid = _suggest(project, extraction_id, '{"n": 42}', evidence=[{"quote": "q", "page": 1}])
    assert owner.post(_url(project, f"/{sid}/accept")).status_code == 200
    after_accept = _row(extraction_id)

    response = owner.post(_url(project, f"/{sid}/undo"))

    assert response.status_code == 422 and "no field history" in response.json()["detail"]
    assert _row(extraction_id) == after_accept
