"""Structured context entries can be corrected and retired, never silently lost (X.161)."""
import io
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent.context import select_agent_context
from app.config import get_settings
from app.database import SessionLocal
from app.exit_conditions import ec_1_2
from app.main import app
from app.models import AuditEvent, Project, ProjectMember, ProjectRole, ResearchContextItem

QUESTION = "How does the rapid pace of AI development affect learners?"


def _login(client, tag, name):
    client.post("/api/auth/development/login", json={"email": f"{name}-{tag}@example.com", "display_name": name})


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner = TestClient(app)
    _login(owner, tag, "owner")
    pid = owner.post("/api/projects", json={"title": "Amend context"}).json()["id"]
    reviewer = TestClient(app)
    _login(reviewer, tag, "reviewer")
    reviewer_id = reviewer.get("/api/auth/me").json()["id"]
    with SessionLocal() as db:
        db.add(ProjectMember(project_id=uuid.UUID(pid), user_id=uuid.UUID(reviewer_id), role=ProjectRole.reviewer))
        db.commit()
    return {"owner": owner, "reviewer": reviewer, "pid": pid}


def _add(world, content=QUESTION, kind="question", rationale=None):
    body = {"kind": kind, "content": content}
    if rationale:
        body["rationale"] = rationale
    response = world["owner"].post(f"/api/projects/{world['pid']}/context", json=body)
    assert response.status_code == 201
    return response.json()["id"]


def _url(world, item_id, suffix=""):
    return f"/api/projects/{world['pid']}/context/{item_id}{suffix}"


def _listed(world, **params):
    return world["owner"].get(f"/api/projects/{world['pid']}/context", params=params).json()


def test_an_entry_saved_with_a_typo_can_be_corrected_and_the_old_text_is_kept():
    # Built inline: the point is the history, not the membership setup.
    owner = TestClient(app)
    _login(owner, uuid.uuid4().hex[:8], "typo")
    pid = owner.post("/api/projects", json={"title": "Typo"}).json()["id"]
    item_id = owner.post(f"/api/projects/{pid}/context", json={"kind": "question", "content": "ow does AI change learning?"}).json()["id"]

    fixed = owner.patch(f"/api/projects/{pid}/context/{item_id}", json={"content": "How does AI change learning?"})

    assert fixed.status_code == 200
    assert fixed.json()["content"] == "How does AI change learning?"
    history = fixed.json()["history"]
    assert [entry["content"] for entry in history] == ["ow does AI change learning?"]
    assert history[0]["changed_by"] and history[0]["changed_at"]


def test_amending_records_an_audit_event_with_ids_and_field_names_but_no_text(world):
    item_id = _add(world, rationale="Because learners fall behind")

    world["owner"].patch(_url(world, item_id), json={"content": "A completely different question text", "rationale": "New reason text"})

    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(world["pid"]), AuditEvent.action == "context.amended")).all()
    assert len(events) == 1
    assert events[0].payload_json == {"item_id": item_id, "fields": ["content", "rationale"]}
    assert "different question" not in str(events[0].payload_json) and "learners fall behind" not in str(events[0].payload_json)


def test_an_amendment_that_changes_nothing_is_refused(world):
    item_id = _add(world)

    response = world["owner"].patch(_url(world, item_id), json={"content": QUESTION})

    assert response.status_code == 422
    assert _listed(world)[0]["history"] in (None, [])


def test_an_amendment_with_no_fields_or_an_empty_statement_is_refused(world):
    item_id = _add(world)

    assert world["owner"].patch(_url(world, item_id), json={}).status_code == 422
    assert world["owner"].patch(_url(world, item_id), json={"content": "   "}).status_code == 422


def test_the_kind_cannot_be_changed_by_an_amendment(world):
    item_id = _add(world)

    assert world["owner"].patch(_url(world, item_id), json={"kind": "objective"}).status_code == 422
    assert _listed(world)[0]["kind"] == "question"


def test_a_retired_entry_leaves_the_default_list_but_stays_available_for_audit(world):
    keep = _add(world, "Keep this question")
    gone = _add(world, "Retire this one")

    retired = world["owner"].post(_url(world, gone, "/retire"))

    assert retired.status_code == 200 and retired.json()["retired_at"]
    assert [row["id"] for row in _listed(world)] == [keep]
    everything = {row["id"]: row for row in _listed(world, include_retired="true")}
    assert set(everything) == {keep, gone} and everything[gone]["retired_at"] and everything[gone]["content"] == "Retire this one"


def test_retiring_twice_or_amending_a_retired_entry_is_refused(world):
    item_id = _add(world)
    world["owner"].post(_url(world, item_id, "/retire"))

    assert world["owner"].post(_url(world, item_id, "/retire")).status_code == 409
    assert world["owner"].patch(_url(world, item_id), json={"content": "Edited after retiring"}).status_code == 409


def test_a_retired_question_is_not_given_to_the_agent_and_does_not_count_for_the_scope_check(world):
    item_id = _add(world)
    pid = uuid.UUID(world["pid"])
    with SessionLocal() as db:
        assert ec_1_2(db, db.get(Project, pid)).met is True

    world["owner"].post(_url(world, item_id, "/retire"))

    with SessionLocal() as db:
        rows = db.scalars(select(ResearchContextItem).where(ResearchContextItem.project_id == pid)).all()
        assert select_agent_context(rows) == []
        assert ec_1_2(db, db.get(Project, pid)).met is False


def test_a_retired_entry_is_left_out_of_the_obsidian_export(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "obsidian_export_enabled", True)
    kept = _add(world, "Kept question stays in the vault")
    retired = _add(world, "Retired question must not be exported")
    world["owner"].post(_url(world, retired, "/retire"))

    response = world["owner"].get(f"/api/projects/{world['pid']}/export/obsidian")

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    text = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
    assert kept and "Kept question stays in the vault" in text
    assert "Retired question must not be exported" not in text


def test_retiring_writes_an_audit_event_without_the_text(world):
    item_id = _add(world, "Question whose text must not reach the audit trail")

    world["owner"].post(_url(world, item_id, "/retire"))

    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(world["pid"]), AuditEvent.action == "context.retired")).all()
    assert len(events) == 1 and events[0].payload_json == {"item_id": item_id, "kind": "question"}


def test_a_reviewer_can_neither_amend_nor_retire_and_nothing_changes(world):
    item_id = _add(world)

    assert world["reviewer"].patch(_url(world, item_id), json={"content": "Hijacked wording"}).status_code == 403
    assert world["reviewer"].post(_url(world, item_id, "/retire")).status_code == 403
    row = _listed(world)[0]
    assert row["content"] == QUESTION and row["retired_at"] is None


def test_an_entry_of_another_project_is_not_found_and_not_touched(world):
    other = TestClient(app)
    _login(other, uuid.uuid4().hex[:8], "other")
    other_pid = other.post("/api/projects", json={"title": "Other"}).json()["id"]
    foreign = other.post(f"/api/projects/{other_pid}/context", json={"kind": "question", "content": "Not yours to edit"}).json()["id"]

    assert world["owner"].patch(_url(world, foreign), json={"content": "Overwritten text"}).status_code == 404
    assert world["owner"].post(_url(world, foreign, "/retire")).status_code == 404
    assert other.get(f"/api/projects/{other_pid}/context").json()[0]["content"] == "Not yours to edit"


def test_an_unknown_entry_is_not_found(world):
    missing = str(uuid.uuid4())

    assert world["owner"].patch(_url(world, missing), json={"content": "Nobody home"}).status_code == 404
    assert world["owner"].post(_url(world, missing, "/retire")).status_code == 404


def _delete(world, item_id, client="owner"):
    return world[client].delete(_url(world, item_id))


def _exists(item_id):
    with SessionLocal() as db:
        return db.get(ResearchContextItem, uuid.UUID(item_id)) is not None


def test_a_deleted_entry_is_gone_from_every_view_and_from_the_database(world):
    keep = _add(world, "Keep this question")
    gone = _add(world, "Delete this question")

    assert _delete(world, gone).status_code == 204

    assert [row["id"] for row in _listed(world, include_retired="true")] == [keep]
    assert _exists(gone) is False


def test_a_retired_entry_can_still_be_deleted(world):
    item_id = _add(world)
    world["owner"].post(_url(world, item_id, "/retire"))

    assert _delete(world, item_id).status_code == 204
    assert _exists(item_id) is False


def test_deleting_writes_an_audit_event_with_the_id_and_kind_but_not_the_text(world):
    item_id = _add(world, "Question text that must not reach the audit trail")

    _delete(world, item_id)

    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(world["pid"]), AuditEvent.action == "context.deleted")).all()
    assert len(events) == 1 and events[0].payload_json == {"item_id": item_id, "kind": "question"}


def test_a_reviewer_cannot_delete_and_the_entry_stays(world):
    item_id = _add(world)

    assert _delete(world, item_id, client="reviewer").status_code == 403
    assert _exists(item_id)


def test_an_entry_of_another_project_or_an_unknown_one_is_not_found_and_not_touched(world):
    other = TestClient(app)
    _login(other, uuid.uuid4().hex[:8], "other")
    other_pid = other.post("/api/projects", json={"title": "Other"}).json()["id"]
    foreign = other.post(f"/api/projects/{other_pid}/context", json={"kind": "question", "content": "Not yours to delete"}).json()["id"]

    assert _delete(world, foreign).status_code == 404
    assert _delete(world, str(uuid.uuid4())).status_code == 404
    assert _exists(foreign)


def test_an_entry_another_entry_points_at_is_not_deleted_until_that_one_is_gone(world):
    question = _add(world, "The question a decision is about")
    decision = _add(world, "A decision about the question", kind="decision")
    with SessionLocal() as db:
        db.get(ResearchContextItem, uuid.UUID(decision)).related_item_id = uuid.UUID(question)
        db.commit()

    blocked = _delete(world, question)

    assert blocked.status_code == 409 and "decision" in blocked.json()["detail"].lower()
    assert _exists(question)
    assert _delete(world, decision).status_code == 204
    assert _delete(world, question).status_code == 204


def test_the_idea_selected_in_the_idea_brief_is_not_deleted(world):
    idea = _add(world, "An idea chosen for the brief", kind="idea")
    saved = world["owner"].put(
        f"/api/projects/{world['pid']}/idea-brief",
        json={"problem": "A problem", "why_it_matters": "It matters", "selected_idea_id": idea},
    )
    assert saved.status_code == 200

    blocked = _delete(world, idea)

    assert blocked.status_code == 409 and "idea brief" in blocked.json()["detail"].lower()
    assert _exists(idea)


def test_deleting_an_entry_clears_its_never_for_ai_lock(world):
    from app.models import AiItemLock

    item_id = _add(world)
    assert world["owner"].put(f"/api/projects/{world['pid']}/ai-locks/context_item/{item_id}").status_code == 200

    _delete(world, item_id)

    with SessionLocal() as db:
        left = db.scalars(select(AiItemLock).where(AiItemLock.project_id == uuid.UUID(world["pid"]), AiItemLock.target_id == item_id)).all()
    assert left == []
