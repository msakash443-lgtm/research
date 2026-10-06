"""Plan rule 25: every AI output records the model and the prompt version that produced it."""

import json
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, ResearchRun, ResearchRunStatus
from app.agent import executor
from app.prompt_registry import load_prompt

PINNED = load_prompt(*executor.EVIDENCE_SYNTHESIS_PROMPT).ref  # e.g. evidence_synthesis@3


def _run(email, with_source=True):
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": email, "display_name": "P"})
    project_id = client.post("/api/projects", json={"title": "Provenance"}).json()["id"]
    if with_source:
        source_id = client.post(f"/api/projects/{project_id}/sources", json={"title": "S", "evidence_excerpt": "Participation rose."}).json()["id"]
        client.post(f"/api/projects/{project_id}/sources/{source_id}/verify")  # cited sources must be verified (M1.10.3)
    body = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "A long enough question?"}).json()
    return client, project_id, body


def _finished_event(project_id):
    with SessionLocal() as db:
        events = db.scalars(
            select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(project_id), AuditEvent.action == "research_run.finished")
        ).all()
    assert len(events) == 1
    return events[0]


def test_a_completed_answer_records_prompt_version_and_model_everywhere(fake_llm):
    client, project_id, run = _run("prov-ok@example.com")

    assert run["status"] == "completed" and run["answer"]
    assert run["prompt_version"] == PINNED and run["provider_model"] == "fake-model"
    listed = client.get(f"/api/projects/{project_id}/research-runs").json()[0]
    assert (listed["prompt_version"], listed["provider_model"]) == (PINNED, "fake-model")
    event = _finished_event(project_id)
    assert event.prompt_version == PINNED and event.model_id == "fake-model"


def test_the_request_to_the_model_uses_the_text_of_the_recorded_prompt_version(fake_llm):
    _run("prov-text@example.com")

    body = json.loads(fake_llm.requests[0].content)
    system, user = (m["content"] for m in body["messages"])
    prompt = load_prompt(*executor.EVIDENCE_SYNTHESIS_PROMPT)
    assert system == prompt.system
    assert user.startswith("Research question:\nA long enough question?") and user.endswith("then the most useful next step.")


def test_a_failed_model_call_still_records_which_prompt_and_model_were_used(fake_llm):
    fake_llm.handler = lambda request: httpx.Response(500, json={"error": "down"})
    client, project_id, run = _run("prov-fail@example.com")

    assert run["status"] == "failed" and run["answer"] is None
    assert run["prompt_version"] == PINNED and run["provider_model"] == "fake-model"
    event = _finished_event(project_id)
    assert event.prompt_version == PINNED and event.model_id == "fake-model"


def test_a_run_that_never_reached_the_model_records_neither(fake_llm):
    client, project_id, run = _run("prov-none@example.com", with_source=False)

    # (The guidance sentence a needs_sources run stores in `answer` is system text, not model output.)
    assert run["status"] == "needs_sources"
    assert run["prompt_version"] is None and run["provider_model"] is None
    assert fake_llm.requests == []


def test_no_completed_answer_is_missing_its_provenance(fake_llm):
    _run("prov-a@example.com")
    _run("prov-b@example.com", with_source=False)

    with SessionLocal() as db:
        model_answers = [r for r in db.scalars(select(ResearchRun)) if r.status == ResearchRunStatus.completed]
    assert model_answers, "the check below must have something to look at"
    assert all(r.answer and r.prompt_version and r.provider_model for r in model_answers)
