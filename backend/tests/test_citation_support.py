"""A quote match is mechanical; a support judgment always belongs to a person."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Source
from app import citation_support
from tests.llm_replies import chat_reply
from tests.test_fulltext import make_pdf


def _world(fake_llm):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={
        "email": f"citation-support-{uuid.uuid4().hex}@example.com", "display_name": "Reader"})
    project_id = client.post("/api/projects", json={"title": "Citation support"}).json()["id"]
    source_id = client.post(f"/api/projects/{project_id}/sources", json={
        "title": "A study", "evidence_excerpt": "The observed effect increased.", "locator": "p. 1"}).json()["id"]
    assert client.post(f"/api/projects/{project_id}/sources/{source_id}/verify").status_code == 200
    fake_llm.handler = lambda request: chat_reply("The effect increased [S1].", 0.8)
    return client, project_id, source_id


def test_research_run_quote_match_does_not_confirm_semantic_support(fake_llm):
    client, project_id, _ = _world(fake_llm)
    run = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "What changed?"})
    assert run.status_code == 202 and run.json()["status"] == "completed", run.text
    claims = client.get(f"/api/projects/{project_id}/claims", params={"run_id": run.json()["id"]}).json()
    assert len(claims) == 1
    link = claims[0]["evidence"][0]
    assert link["quote_matches_source"] is True
    assert link["support_confirmed_by"] is None and link["support_confirmed_at"] is None
    assert claims[0]["verified"] is False

    endpoint = f"/api/projects/{project_id}/claims/{claims[0]['id']}/evidence/{link['id']}/confirm-support"
    confirmed = client.post(endpoint)
    assert confirmed.status_code == 200, confirmed.text
    record = confirmed.json()["evidence"][0]
    assert uuid.UUID(record["support_confirmed_by"])
    assert record["support_confirmed_at"] is not None
    assert confirmed.json()["verified"] is False  # support confirmation is distinct from claim verification
    assert client.post(endpoint).json()["evidence"][0]["support_confirmed_at"].rstrip("Z") == record["support_confirmed_at"].rstrip("Z")
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "citation.support_confirmed")).all()
        assert len(events) == 1
        assert events[0].payload_json["evidence_id"] == link["id"]


def test_pdf_mismatch_stays_unchecked_until_human_confirms(fake_llm, monkeypatch):
    client, project_id, source_id = _world(fake_llm)
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(source_id)).fulltext_path = "stored-test-pdf"
        db.commit()

    class Store:
        def get(self, key):
            assert key == "stored-test-pdf"
            return make_pdf([["Different words appear in the stored PDF."]])

    monkeypatch.setattr(citation_support, "get_object_store", lambda settings: Store())
    run = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "What changed?"})
    assert run.json()["status"] == "completed", run.text
    claim = client.get(f"/api/projects/{project_id}/claims", params={"run_id": run.json()["id"]}).json()[0]
    link = claim["evidence"][0]
    assert link["quote_matches_source"] is False
    assert link["support_confirmed_by"] is None
    confirmed = client.post(f"/api/projects/{project_id}/claims/{claim['id']}/evidence/{link['id']}/confirm-support")
    assert confirmed.status_code == 200
    assert confirmed.json()["evidence"][0]["support_confirmed_by"] is not None


def test_confirmation_cannot_cross_project_or_use_another_claims_evidence(fake_llm):
    client, project_id, _ = _world(fake_llm)
    run = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "What changed?"}).json()
    claim = client.get(f"/api/projects/{project_id}/claims", params={"run_id": run["id"]}).json()[0]
    link_id = claim["evidence"][0]["id"]
    other = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    assert client.post(f"/api/projects/{other}/claims/{claim['id']}/evidence/{link_id}/confirm-support").status_code == 404
    assert client.post(f"/api/projects/{project_id}/claims/{claim['id']}/evidence/{uuid.uuid4()}/confirm-support").status_code == 404
