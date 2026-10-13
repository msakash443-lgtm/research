"""Claims require a source excerpt and synthesis markers resolve to the run snapshot."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Claim, ClaimEvidence, SourceExcerpt
from tests.llm_replies import chat_reply


def _project():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "claims@example.com", "display_name": "C"})
    project_id = client.post("/api/projects", json={"title": "Claims"}).json()["id"]
    return client, project_id


def _source(client, project_id, title="Study"):
    source_id = client.post(f"/api/projects/{project_id}/sources", json={
        "title": title, "evidence_excerpt": "The observed effect increased.", "locator": "p. 2",
    }).json()["id"]
    with SessionLocal() as db:
        excerpt_id = db.scalar(select(SourceExcerpt.id).where(SourceExcerpt.source_id == uuid.UUID(source_id)))
    return source_id, str(excerpt_id)


def test_a_claim_without_evidence_cannot_be_verified_through_api_or_database():
    client, project_id = _project()
    response = client.post(f"/api/projects/{project_id}/claims", json={"text": "Unsupported statement"})
    assert response.status_code == 201, response.text
    claim_id = response.json()["id"]
    refused = client.post(f"/api/projects/{project_id}/claims/{claim_id}/verify")
    assert refused.status_code == 409
    with SessionLocal() as db:
        with pytest.raises(IntegrityError, match="verified claim requires evidence"):
            db.execute(update(Claim).where(Claim.id == uuid.UUID(claim_id)).values(verified=True))
            db.commit()
        db.rollback()
        existing = db.get(Claim, uuid.UUID(claim_id))
        with pytest.raises(IntegrityError, match="verified claim requires evidence"):
            db.add(Claim(project_id=existing.project_id, text="Inserted as verified",
                         verified=True, created_by="test"))
            db.commit()
        db.rollback()


def test_a_verified_claim_cannot_lose_its_last_evidence():
    client, project_id = _project()
    source_id, excerpt_id = _source(client, project_id)
    client.post(f"/api/projects/{project_id}/sources/{source_id}/verify")
    response = client.post(f"/api/projects/{project_id}/claims", json={
        "text": "The effect increased.",
        "evidence": [{"source_id": source_id, "excerpt_id": excerpt_id,
                      "quote": "The observed effect increased."}],
    })
    assert response.status_code == 201, response.text
    claim_id = response.json()["id"]
    verified = client.post(f"/api/projects/{project_id}/claims/{claim_id}/verify")
    assert verified.status_code == 200 and verified.json()["verified"] is True
    with SessionLocal() as db:
        with pytest.raises(IntegrityError, match="verified claim requires evidence"):
            db.execute(delete(ClaimEvidence).where(ClaimEvidence.claim_id == uuid.UUID(claim_id)))
            db.commit()
        db.rollback()
    second = client.post(f"/api/projects/{project_id}/claims", json={"text": "Another claim"}).json()["id"]
    with SessionLocal() as db:
        with pytest.raises(IntegrityError, match="verified claim requires evidence"):
            db.execute(update(ClaimEvidence).where(ClaimEvidence.claim_id == uuid.UUID(claim_id))
                       .values(claim_id=uuid.UUID(second)))
            db.commit()
        db.rollback()


def test_claim_evidence_must_belong_to_the_project_and_quote_its_excerpt():
    client, project_id = _project()
    source_id, excerpt_id = _source(client, project_id)
    bad_quote = client.post(f"/api/projects/{project_id}/claims", json={
        "text": "Claim", "evidence": [{"source_id": source_id, "excerpt_id": excerpt_id, "quote": "invented"}],
    })
    assert bad_quote.status_code == 422
    other = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    wrong_project = client.post(f"/api/projects/{other}/claims", json={
        "text": "Claim", "evidence": [{"source_id": source_id, "excerpt_id": excerpt_id,
                                          "quote": "The observed effect increased."}],
    })
    assert wrong_project.status_code == 422


def test_synthesis_creates_unverified_claims_linked_to_snapshot_excerpts(fake_llm):
    client, project_id = _project()
    source_id, excerpt_id = _source(client, project_id)
    client.post(f"/api/projects/{project_id}/sources/{source_id}/verify")
    fake_llm.handler = lambda request: chat_reply("The effect increased [S1].", 0.7)
    run = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "What changed?"})
    assert run.status_code == 202 and run.json()["status"] == "completed", run.text
    claims = client.get(f"/api/projects/{project_id}/claims").json()
    assert len(claims) == 1
    assert claims[0]["run_id"] == run.json()["id"] and claims[0]["verified"] is False
    assert claims[0]["evidence"][0]["source_id"] == source_id
    assert claims[0]["evidence"][0]["excerpt_id"] == excerpt_id
    with SessionLocal() as db:
        saved_run = db.get(Claim, uuid.UUID(claims[0]["id"]))
        assert saved_run.run_id == uuid.UUID(run.json()["id"])
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "claim.created")) is not None


def test_synthesis_keeps_a_trailing_citation_with_its_sentence(fake_llm):
    client, project_id = _project()
    source_id, _ = _source(client, project_id)
    client.post(f"/api/projects/{project_id}/sources/{source_id}/verify")
    fake_llm.handler = lambda request: chat_reply("The effect increased. [S1]", 0.7)
    run = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "What changed?"})
    assert run.json()["status"] == "completed", run.text
    claims = client.get(f"/api/projects/{project_id}/claims").json()
    assert claims[0]["text"] == "The effect increased. [S1]"


def test_unknown_marker_blocks_completion_and_leaves_no_claims(fake_llm):
    client, project_id = _project()
    source_id, _ = _source(client, project_id)
    client.post(f"/api/projects/{project_id}/sources/{source_id}/verify")
    fake_llm.handler = lambda request: chat_reply("The effect increased [S2].", 0.7)
    run = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "What changed?"})
    assert run.json()["status"] == "failed" and run.json()["answer"] is None
    assert client.get(f"/api/projects/{project_id}/claims").json() == []
