"""Inbox triage and promotion (X.31.2).

A note is private to its author until it is filed to a project (Q16): then every project member may read it,
and only the author may change it. Promotion creates a research context item that keeps a link back to the note.
"""

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent.context import select_agent_context
from app.database import SessionLocal
from app.main import app
from app.models import AiItemLock, AuditEvent, ContextKind, ResearchContextItem


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "T"}).json()["id"]
    return client, user_id


def _project(client, title="Gender wage inequality"):
    return client.post("/api/projects", json={"title": title}).json()["id"]


def _capture(client, body="Does remote work widen the gap?", ai_locked=False):
    client_id = f"c-{uuid.uuid4()}"
    response = client.post("/api/notes", json={"client_id": client_id, "kind": "typed", "body": body, "ai_locked": ai_locked})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _file(client, note_id, project_id):
    return client.post(f"/api/notes/{note_id}/file", json={"project_id": project_id})


def _promote(client, note_id, kind, content, **extra):
    return client.post(f"/api/notes/{note_id}/promote", json={"kind": kind, "content": content, **extra})


def _invite(owner, project_id, email, role):
    # The invitee must have signed in once so that the account exists.
    _login(email)
    response = owner.post(f"/api/projects/{project_id}/members", json={"email": email, "role": role})
    assert response.status_code == 201, response.text


def test_unfiled_note_is_invisible_to_everyone_but_its_author():
    author, _ = _login("triage-private-author@example.com")
    note_id = _capture(author)
    project_id = _project(author)
    _invite(author, project_id, "triage-private-reader@example.com", "reviewer")
    reader, _ = _login("triage-private-reader@example.com")

    assert reader.get(f"/api/notes/{note_id}").status_code == 404
    assert reader.get(f"/api/notes/{note_id}/revisions").status_code == 404


def test_filed_note_is_readable_by_project_members_but_not_editable():
    author, _ = _login("triage-read-author@example.com")
    project_id = _project(author)
    note_id = _capture(author)
    assert _file(author, note_id, project_id).status_code == 200
    _invite(author, project_id, "triage-read-reviewer@example.com", "reviewer")
    reviewer, _ = _login("triage-read-reviewer@example.com")

    assert reviewer.get(f"/api/notes/{note_id}").status_code == 200
    assert reviewer.get(f"/api/notes/{note_id}/revisions").status_code == 200
    # Read-only: a member may not edit, file, archive or promote someone else's note.
    assert reviewer.patch(f"/api/notes/{note_id}", json={"base_revision": 1, "body": "mine now"}).status_code == 404
    assert reviewer.post(f"/api/notes/{note_id}/archive").status_code == 404
    assert _file(reviewer, note_id, project_id).status_code == 404
    assert _promote(reviewer, note_id, "idea", "Idea").status_code == 404


def test_filing_needs_a_write_role_on_the_project():
    owner, _ = _login("triage-file-owner@example.com")
    project_id = _project(owner)
    _invite(owner, project_id, "triage-file-reviewer@example.com", "reviewer")
    reviewer, _ = _login("triage-file-reviewer@example.com")
    note_id = _capture(reviewer)

    response = _file(reviewer, note_id, project_id)

    assert response.status_code == 404
    assert reviewer.get(f"/api/notes/{note_id}").json()["status"] == "inbox"


def test_filing_moves_the_note_out_of_the_inbox_and_is_audited():
    client, _ = _login("triage-file-audit@example.com")
    project_id = _project(client)
    note_id = _capture(client)

    response = _file(client, note_id, project_id)

    assert response.status_code == 200
    assert response.json()["status"] == "filed"
    assert response.json()["project_id"] == project_id
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "note.filed")).all()
        assert any(event.payload_json.get("note_id") == note_id for event in events)


def test_promotion_needs_the_note_to_be_filed_first():
    client, _ = _login("triage-unfiled@example.com")
    note_id = _capture(client)

    response = _promote(client, note_id, "hypothesis", "Remote work widens the gap")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "note_not_filed"


def test_promotion_creates_a_context_item_linked_to_the_note():
    client, _ = _login("triage-promote@example.com")
    project_id = _project(client)
    note_id = _capture(client)
    _file(client, note_id, project_id)

    response = _promote(client, note_id, "hypothesis", "Remote work widens the gap", rationale="Sen 2019")

    assert response.status_code == 201, response.text
    item = response.json()
    assert item["kind"] == "hypothesis"
    assert item["content"] == "Remote work widens the gap"
    assert item["rationale"] == "Sen 2019"
    assert item["promoted_from_note_id"] == note_id
    assert item["related_item_id"] is None
    listed = client.get(f"/api/projects/{project_id}/context").json()
    assert [row["id"] for row in listed] == [item["id"]]


def test_promotion_accepts_only_the_triage_kinds():
    client, _ = _login("triage-kinds@example.com")
    project_id = _project(client)
    note_id = _capture(client)
    _file(client, note_id, project_id)

    # question and objective are research-design items, not promotion targets.
    assert _promote(client, note_id, "question", "Why?").status_code == 422
    assert _promote(client, note_id, "methodology", "Survey").status_code == 422


def test_a_decision_can_point_at_the_item_it_concerns_in_the_same_project():
    client, _ = _login("triage-related@example.com")
    project_id = _project(client)
    note_id = _capture(client)
    _file(client, note_id, project_id)
    hypothesis_id = _promote(client, note_id, "hypothesis", "H2: care burden explains the gap").json()["id"]

    response = _promote(client, note_id, "decision", "Dropped H2", rationale="No effect in pilot", related_item_id=hypothesis_id)

    assert response.status_code == 201, response.text
    assert response.json()["related_item_id"] == hypothesis_id


def test_a_related_item_from_another_project_is_refused():
    client, _ = _login("triage-cross@example.com")
    project_id = _project(client, "First")
    other_project = _project(client, "Second")
    note_id = _capture(client)
    _file(client, note_id, project_id)
    foreign_item = client.post(f"/api/projects/{other_project}/context", json={"kind": "hypothesis", "content": "Elsewhere"}).json()["id"]

    response = _promote(client, note_id, "decision", "Dropped", related_item_id=foreign_item)

    assert response.status_code == 422


def test_decision_log_is_the_decision_filter_on_the_context_list():
    client, _ = _login("triage-log@example.com")
    project_id = _project(client)
    note_id = _capture(client)
    _file(client, note_id, project_id)
    _promote(client, note_id, "hypothesis", "H1")
    _promote(client, note_id, "decision", "Chose survey design")

    decisions = client.get(f"/api/projects/{project_id}/context?kind=decision").json()

    assert [row["content"] for row in decisions] == ["Chose survey design"]


def test_promoting_a_locked_note_locks_the_new_item_too():
    client, _ = _login("triage-lock@example.com")
    project_id = _project(client)
    note_id = _capture(client, ai_locked=True)
    _file(client, note_id, project_id)
    item_id = _promote(client, note_id, "idea", "Locked idea").json()["id"]

    listed = {row["id"]: row for row in client.get(f"/api/projects/{project_id}/context").json()}

    assert listed[item_id]["ai_locked"] is True
    with SessionLocal() as db:
        assert db.scalar(select(AiItemLock.id).where(AiItemLock.target_id == item_id)) is not None


def test_promoting_an_unlocked_note_does_not_lock_the_item():
    client, _ = _login("triage-unlocked@example.com")
    project_id = _project(client)
    note_id = _capture(client)
    _file(client, note_id, project_id)
    item_id = _promote(client, note_id, "idea", "Open idea").json()["id"]

    listed = {row["id"]: row for row in client.get(f"/api/projects/{project_id}/context").json()}

    assert listed[item_id]["ai_locked"] is False


def test_gap_claim_and_memo_items_are_never_given_to_the_agent():
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    items = [
        ResearchContextItem(id=uuid.uuid4(), project_id=uuid.uuid4(), kind=kind, content=kind.value, created_at=now)
        for kind in (ContextKind.gap, ContextKind.claim, ContextKind.memo, ContextKind.hypothesis)
    ]

    chosen = select_agent_context(items)

    assert [item.kind for item in chosen] == [ContextKind.hypothesis]


def test_suggestions_are_for_the_author_and_only_name_projects_they_belong_to():
    author, _ = _login("triage-sugg-author@example.com")
    project_id = _project(author, "Gender wage inequality")
    stranger, _ = _login("triage-sugg-stranger@example.com")
    _project(stranger, "Gender wage inequality archive")
    note_id = _capture(author, "Remote work and the gender wage gap")

    response = author.get(f"/api/notes/{note_id}/suggestions")

    assert response.status_code == 200
    projects = [item for item in response.json() if item["kind"] == "project"]
    assert [item["target_id"] for item in projects] == [project_id]
    assert stranger.get(f"/api/notes/{note_id}/suggestions").status_code == 404
