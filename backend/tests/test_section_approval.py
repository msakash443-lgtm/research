"""Per-section approval gate (G10, M6.11, spec 5.11 / 8).

Approving a section freezes its AI-involvement level from a current section provenance report. The
project-wide G10 gate needs every section approved; a section saved again after approval goes stale and no
longer counts. Approvals are append-only.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Document, DocumentVersion, Project, ProjectMember, ProjectRole, SectionApproval
from gate_helpers import approve_earlier_gates


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "A"}).json()["id"]
    return client, user_id


def _project(client, title="Manuscript"):
    return client.post("/api/projects", json={"title": title}).json()["id"]


def _document(project_id, owner_id, title="Introduction", position=1):
    with SessionLocal() as db:
        document = Document(project_id=uuid.UUID(project_id), title=title, position=position, created_by=f"user:{owner_id}")
        db.add(document)
        db.commit()
        return str(document.id)


def _doc(*blocks):
    return {"type": "doc", "content": list(blocks)}


def _typed(text, sid, by):
    prov = {"sid": sid, "origin": "typed", "by": by, "sug": None, "note": None, "paste": None, "src": None}
    return {"type": "paragraph", "content": [{"type": "text", "text": text, "marks": [{"type": "prov", "attrs": prov}]}]}


def _save(client, project_id, document_id, content, base):
    return client.post(
        f"/api/projects/{project_id}/documents/{document_id}/versions",
        json={"base_number": base, "client_save_id": uuid.uuid4().hex, "client_session_id": "s1", "content_json": content},
    )


def _report(client, project_id, document_id):
    response = client.post(f"/api/projects/{project_id}/provenance-reports", json={"scope": "section", "document_id": document_id})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def writer():
    client, user_id = _login(f"writer.approval.{uuid.uuid4().hex[:8]}@example.org")
    project_id = _project(client)
    intro = _document(project_id, user_id, "Introduction", 1)
    assert _save(client, project_id, intro, _doc(_typed("Remote work changed wages.", "s1", user_id)), 0).status_code == 201
    return SimpleNamespace(client=client, project_id=project_id, intro=intro, user_id=user_id)


def test_approving_without_a_current_report_is_refused(writer):
    response = writer.client.post(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approve")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_current_report"


def test_approving_freezes_the_ai_share_from_the_current_report(writer):
    _report(writer.client, writer.project_id, writer.intro)
    response = writer.client.post(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approve", json={"note": "Looks right."})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["document_id"] == writer.intro
    assert body["version_number"] == 1
    assert body["ai_share_pct"] == 0.0  # the fixture's text is all typed, no AI spans
    assert body["note"] == "Looks right."
    assert body["stale"] is False


def test_approving_works_from_a_report_that_excluded_the_ai_and_produced_sections(writer):
    # X.31.24: the researcher can hide the "AI contributions"/"How produced" sections from a report's display,
    # but the full breakdown is always kept on the row (section_approval.py reads it directly), so approval
    # still freezes the correct ai_share_pct/ai_shares regardless of what the report chose to show.
    response = writer.client.post(
        f"/api/projects/{writer.project_id}/provenance-reports",
        json={"scope": "section", "document_id": writer.intro, "sections": ["pasted", "statement", "limits"]},
    )
    assert response.status_code == 201, response.text
    assert "ai" not in response.json()["report"] and "shares" not in response.json()["report"]

    approval = writer.client.post(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approve").json()
    assert approval["ai_share_pct"] == 0.0
    assert approval["ai_shares"]["typed"] == 100.0



def test_approving_a_second_time_writes_a_new_row_not_an_edit(writer):
    _report(writer.client, writer.project_id, writer.intro)
    first = writer.client.post(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approve").json()
    _report(writer.client, writer.project_id, writer.intro)
    second = writer.client.post(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approve").json()
    assert first["id"] != second["id"]
    with SessionLocal() as db:
        assert db.scalar(select(SectionApproval).where(SectionApproval.id == uuid.UUID(first["id"]))) is not None


def test_get_approval_status_returns_the_current_approval(writer):
    assert writer.client.get(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approval").json() is None
    _report(writer.client, writer.project_id, writer.intro)
    approved = writer.client.post(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approve").json()
    current = writer.client.get(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approval").json()
    assert current["id"] == approved["id"]


def test_saving_an_approved_section_again_makes_its_approval_stale(writer):
    _report(writer.client, writer.project_id, writer.intro)
    writer.client.post(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approve")
    _save(writer.client, writer.project_id, writer.intro, _doc(_typed("A later sentence.", "s2", writer.user_id)), 1)
    assert writer.client.get(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approval").json() is None
    # The stale approval no longer satisfies this section's current (now unreported) version either.
    response = writer.client.post(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approve")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_current_report"


def test_approving_an_unsaved_section_is_refused(writer):
    empty = _document(writer.project_id, writer.user_id, "Empty", 2)
    response = writer.client.post(f"/api/projects/{writer.project_id}/documents/{empty}/approve")
    assert response.status_code == 409


def test_a_reviewer_or_co_author_may_not_approve_a_section(writer):
    _report(writer.client, writer.project_id, writer.intro)
    for role in (ProjectRole.co_author, ProjectRole.reviewer):
        client, user_id = _login(f"section-approve-{role.value}-{uuid.uuid4().hex[:8]}@example.com")
        with SessionLocal() as db:
            db.add(ProjectMember(project_id=uuid.UUID(writer.project_id), user_id=uuid.UUID(user_id), role=role))
            db.commit()
        response = client.post(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approve")
        assert response.status_code == 403


def test_a_supervisor_may_approve_a_section(writer):
    _report(writer.client, writer.project_id, writer.intro)
    client, user_id = _login(f"section-approve-supervisor-{uuid.uuid4().hex[:8]}@example.com")
    with SessionLocal() as db:
        db.add(ProjectMember(project_id=uuid.UUID(writer.project_id), user_id=uuid.UUID(user_id), role=ProjectRole.supervisor))
        db.commit()
    assert client.post(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approve").status_code == 201


def test_approval_is_audited(writer):
    _report(writer.client, writer.project_id, writer.intro)
    body = writer.client.post(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approve").json()
    with SessionLocal() as db:
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(writer.project_id), AuditEvent.action == "section.approved")
        )
        assert event is not None
        assert event.payload_json["document_id"] == writer.intro
        assert event.payload_json["ai_share_pct"] == body["ai_share_pct"]


def test_approval_rows_cannot_be_edited(writer):
    _report(writer.client, writer.project_id, writer.intro)
    row_id = writer.client.post(f"/api/projects/{writer.project_id}/documents/{writer.intro}/approve").json()["id"]
    with SessionLocal() as db:
        row = db.get(SectionApproval, uuid.UUID(row_id))
        row.note = "edited"
        with pytest.raises(Exception):
            db.commit()


def test_g10_is_refused_while_the_manuscript_has_no_sections():
    client, user_id = _login(f"g10-empty-{uuid.uuid4().hex[:8]}@example.com")
    project_id = _project(client, "Empty manuscript")
    approve_earlier_gates(project_id, "G10")
    response = client.post(f"/api/projects/{project_id}/gates/G10/approve")
    assert response.status_code == 409
    assert "at least one section" in response.json()["detail"]


def test_g10_is_refused_while_a_section_is_unapproved():
    client, user_id = _login(f"g10-unapproved-{uuid.uuid4().hex[:8]}@example.com")
    project_id = _project(client, "Unapproved section")
    intro = _document(project_id, user_id, "Introduction", 1)
    _save(client, project_id, intro, _doc(_typed("Not yet approved.", "s1", user_id)), 0)
    approve_earlier_gates(project_id, "G10")
    response = client.post(f"/api/projects/{project_id}/gates/G10/approve")
    assert response.status_code == 409
    assert response.json()["detail"]["documents"][0]["document_id"] == intro


def test_g10_approves_once_every_section_is_approved():
    client, user_id = _login(f"g10-approved-{uuid.uuid4().hex[:8]}@example.com")
    project_id = _project(client, "Fully approved")
    intro = _document(project_id, user_id, "Introduction", 1)
    methods = _document(project_id, user_id, "Methods", 2)
    for document_id, text in ((intro, "Intro text."), (methods, "Methods text.")):
        _save(client, project_id, document_id, _doc(_typed(text, f"s-{document_id}", user_id)), 0)
        _report(client, project_id, document_id)
        assert client.post(f"/api/projects/{project_id}/documents/{document_id}/approve").status_code == 201
    approve_earlier_gates(project_id, "G10")
    response = client.post(f"/api/projects/{project_id}/gates/G10/approve")
    assert response.status_code == 200, response.text


def test_g10_is_refused_again_once_an_approved_section_is_resaved():
    client, user_id = _login(f"g10-stale-{uuid.uuid4().hex[:8]}@example.com")
    project_id = _project(client, "Resaved after approval")
    intro = _document(project_id, user_id, "Introduction", 1)
    _save(client, project_id, intro, _doc(_typed("Intro text.", "s1", user_id)), 0)
    _report(client, project_id, intro)
    assert client.post(f"/api/projects/{project_id}/documents/{intro}/approve").status_code == 201
    _save(client, project_id, intro, _doc(_typed("Revised intro text.", "s2", user_id)), 1)
    approve_earlier_gates(project_id, "G10")
    response = client.post(f"/api/projects/{project_id}/gates/G10/approve")
    assert response.status_code == 409
