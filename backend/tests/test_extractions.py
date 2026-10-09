import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Extraction, Gate, GateCode, GateStatus, ProjectMember, ProjectRole


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "X"}).json()["id"]
    return client, user_id


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner, owner_id = _login(f"extract-owner-{tag}@example.com")
    pid = owner.post("/api/projects", json={"title": "Extraction"}).json()["id"]
    reviewer, reviewer_id = _login(f"extract-reviewer-{tag}@example.com")
    with SessionLocal() as db:
        db.add(ProjectMember(project_id=uuid.UUID(pid), user_id=uuid.UUID(reviewer_id), role=ProjectRole.reviewer))
        db.commit()
    sid = owner.post(f"/api/projects/{pid}/sources", json={"title": "A paper"}).json()["id"]
    return {"owner": owner, "owner_id": owner_id, "reviewer": reviewer, "pid": pid, "sid": sid}


GOOD = {
    "fields": {"research_question": "Does remote work raise output?", "sample": {"n": 120, "population": "clerks", "country": "NL"}, "limitations": None},
    "evidence": {
        "research_question": {"quote": "We ask whether remote work raises output", "page": 2, "section": "Introduction"},
        "sample": {"quote": "120 clerks in the Netherlands", "page": None, "section": "Method"},
    },
}


def create(world, body=None, **extra):
    return world["owner"].post(f"/api/projects/{world['pid']}/extractions", json={"source_id": world["sid"], **(body or GOOD), **extra})


def test_a_well_formed_extraction_is_stored_unverified_with_its_schema_version(world):
    r = create(world)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["schema_version"] == "default@1"
    assert data["fields_json"] == GOOD["fields"] and data["evidence_spans"] == GOOD["evidence"]
    assert data["verified_by_human"] is False and data["verified_by"] is None
    assert data["extracted_by"] == "human" and data["extractor"] == world["owner_id"]
    assert data["model_id"] is None and data["prompt_version"] is None

    listed = world["reviewer"].get(f"/api/projects/{world['pid']}/extractions", params={"source_id": world["sid"]}).json()
    assert [e["id"] for e in listed] == [data["id"]]
    assert world["reviewer"].get(f"/api/projects/{world['pid']}/extractions/{data['id']}").json() == data


def test_a_value_without_evidence_is_refused_with_every_problem(world):
    body = {
        "fields": {"research_question": "Why?", "sample": {"n": 1, "population": "adults", "country": "UK"}},
        "evidence": {"sample": {"quote": "  ", "page": 1}},
    }
    r = create(world, body)
    assert r.status_code == 422
    assert r.json()["detail"] == [
        "research_question: a non-null field needs evidence {quote, page, section}",
        "sample: evidence needs a non-empty quote",
    ]
    assert world["owner"].get(f"/api/projects/{world['pid']}/extractions").json() == []


def test_multibyte_evidence_is_counted_by_utf8_bytes_for_the_payload_limit(world):
    body = {
        "fields": {"research_question": "Q"},
        "evidence": {"research_question": {"quote": "界" * 60_000, "page": 1}},
    }
    response = create(world, body)
    assert response.status_code == 201, response.text


def test_payload_larger_than_the_utf8_byte_limit_is_refused(world):
    body = {
        "fields": {"research_question": "Q"},
        "evidence": {"research_question": {"quote": "界" * 70_000, "page": 1}},
    }
    assert create(world, body).status_code == 413


@pytest.mark.parametrize(
    "body",
    [
        {"fields": {"not_a_field": "x"}},  # unknown key
        {"fields": {"sample": "120"}, "evidence": {"sample": {"quote": "120", "page": 1}}},  # wrong type
        {"fields": {"sample": None}, "evidence": {"sample": {"quote": "x", "page": 1}}},  # evidence for an empty field
        {"fields": {"sample": {"n": 1}}, "evidence": {"sample": {"quote": "x"}}},  # no page or section
    ],
)
def test_schema_violations_are_refused(world, body):
    assert create(world, body).status_code == 422


def test_unknown_schema_and_another_projects_source_are_refused(world):
    assert create(world, schema_name="nope").status_code == 422
    assert create(world, schema_name="../default").status_code == 422
    other = world["owner"].post("/api/projects", json={"title": "Other"}).json()["id"]
    foreign = world["owner"].post(f"/api/projects/{other}/sources", json={"title": "Elsewhere"}).json()["id"]
    assert create(world, source_id=foreign).status_code == 404


def test_the_client_cannot_mark_an_extraction_verified(world):
    assert create(world, verified_by_human=True).status_code == 422  # extra keys are rejected
    eid = create(world).json()["id"]
    r = world["owner"].put(f"/api/projects/{world['pid']}/extractions/{eid}", json={**GOOD, "verified_by_human": True})
    assert r.status_code == 422


def test_an_edit_replaces_fields_and_is_checked_again(world):
    eid = create(world).json()["id"]
    url = f"/api/projects/{world['pid']}/extractions/{eid}"
    assert world["owner"].put(url, json={"fields": {"method": {"design": "RCT"}}}).status_code == 422  # no evidence
    new = {
        "fields": {"method": {"design": "RCT", "analysis": "intention-to-treat"}},
        "evidence": {"method": {"quote": "a randomised trial", "section": "Design"}},
    }
    r = world["owner"].put(url, json=new)
    assert r.status_code == 200, r.text
    assert r.json()["fields_json"] == new["fields"] and r.json()["evidence_spans"] == new["evidence"]


def test_a_verified_extraction_cannot_be_edited(world):
    eid = create(world).json()["id"]
    with SessionLocal() as db:
        row = db.get(Extraction, uuid.UUID(eid))
        row.verified_by_human, row.verified_by, row.verified_at = True, world["owner_id"], datetime.now(timezone.utc)
        db.commit()
    r = world["owner"].put(f"/api/projects/{world['pid']}/extractions/{eid}", json=GOOD)
    assert r.status_code == 409


def test_an_extraction_made_with_an_older_schema_version_cannot_be_edited_in_place(world):
    eid = create(world).json()["id"]
    with SessionLocal() as db:
        db.get(Extraction, uuid.UUID(eid)).schema_version = "default@0"
        db.commit()
    assert world["owner"].put(f"/api/projects/{world['pid']}/extractions/{eid}", json=GOOD).status_code == 409


def test_the_database_refuses_verified_without_a_verifier(world):
    eid = create(world).json()["id"]
    with SessionLocal() as db:
        db.get(Extraction, uuid.UUID(eid)).verified_by_human = True
        with pytest.raises(IntegrityError):
            db.commit()


def test_approved_g4_locks_extractions(world):
    eid = create(world).json()["id"]
    with SessionLocal() as db:
        gate = db.scalar(select(Gate).where(Gate.project_id == uuid.UUID(world["pid"]), Gate.code == GateCode.G4))
        gate.status, gate.decided_by = GateStatus.approved, world["owner_id"]
        db.commit()
    assert create(world).status_code == 409
    assert world["owner"].put(f"/api/projects/{world['pid']}/extractions/{eid}", json=GOOD).status_code == 409


def test_create_and_edit_are_audited(world):
    eid = create(world).json()["id"]
    world["owner"].put(f"/api/projects/{world['pid']}/extractions/{eid}", json={"fields": {"limitations": None}})
    with SessionLocal() as db:
        events = {e.action: e.payload_json for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(world["pid"]), AuditEvent.action.like("extraction.%")))}
    assert set(events) == {"extraction.created", "extraction.updated"}
    assert events["extraction.created"]["fields"] == ["research_question", "sample"]
    assert events["extraction.updated"]["fields_before"] == ["research_question", "sample"] and events["extraction.updated"]["fields"] == []


def test_a_reviewer_cannot_write(world):
    r = world["reviewer"].post(f"/api/projects/{world['pid']}/extractions", json={"source_id": world["sid"], **GOOD})
    assert r.status_code == 403
