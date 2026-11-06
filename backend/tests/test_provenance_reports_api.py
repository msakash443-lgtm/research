"""Writing provenance report API (X.31.12).

A report freezes the latest saved versions it covers. Saving a covered section again marks the report's
Artifact stale; saving an uncovered section does not. Reports are append-only.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Artifact, ArtifactStatus, AuditEvent, Document, ProvenanceReport


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "D"}).json()["id"]
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
        json={"base_number": base, "client_save_id": uuid.uuid4().hex, "client_session_id": "session-1", "content_json": content},
    )


def _report(client, project_id, **body):
    return client.post(f"/api/projects/{project_id}/provenance-reports", json=body)


@pytest.fixture
def writer():
    client, user_id = _login("writer.provenance@example.org")
    project_id = _project(client)
    actor = user_id  # the ledger records the bare user id as the actor
    intro = _document(project_id, user_id, "Introduction", 1)
    methods = _document(project_id, user_id, "Methods", 2)
    assert _save(client, project_id, intro, _doc(_typed("Remote work changed wages.", "s1", actor)), 0).status_code == 201
    assert _save(client, project_id, methods, _doc(_typed("We used panel data.", "s2", actor)), 0).status_code == 201
    return SimpleNamespace(client=client, project_id=project_id, intro=intro, methods=methods, actor=actor, user_id=user_id)


def test_section_report_covers_the_latest_saved_version(writer):
    response = _report(writer.client, writer.project_id, scope="section", document_id=writer.intro)

    assert response.status_code == 201
    body = response.json()
    assert body["scope"] == "section"
    assert [v["number"] for v in body["covered_versions"]] == [1]
    assert body["report"]["shares"]["typed"] == 100.0
    assert body["statement_template"] == "ai_use_statement@2"
    assert len(body["report_hash"]) == 64


def test_manuscript_report_covers_every_section(writer):
    body = _report(writer.client, writer.project_id, scope="manuscript").json()

    assert sorted(v["title"] for v in body["covered_versions"]) == ["Introduction", "Methods"]


def test_manuscript_report_can_be_scoped_to_chosen_sections(writer):
    body = _report(writer.client, writer.project_id, scope="manuscript", section_ids=[writer.intro]).json()

    assert [v["title"] for v in body["covered_versions"]] == ["Introduction"]
    assert body["report"]["total_chars"] == len("Remote work changed wages.")


def test_manuscript_report_with_no_filter_matches_the_unfiltered_total(writer):
    filtered = _report(writer.client, writer.project_id, scope="manuscript", section_ids=[writer.intro, writer.methods]).json()
    unfiltered = _report(writer.client, writer.project_id, scope="manuscript").json()

    assert sorted(v["title"] for v in filtered["covered_versions"]) == sorted(v["title"] for v in unfiltered["covered_versions"])
    assert filtered["report"]["total_chars"] == unfiltered["report"]["total_chars"]


def test_section_ids_must_be_non_empty_when_given(writer):
    response = _report(writer.client, writer.project_id, scope="manuscript", section_ids=[])
    assert response.status_code == 422


def test_section_ids_must_belong_to_the_project(writer):
    response = _report(writer.client, writer.project_id, scope="manuscript", section_ids=[str(uuid.uuid4())])
    assert response.status_code == 404


def test_section_report_refuses_section_ids(writer):
    response = _report(writer.client, writer.project_id, scope="section", document_id=writer.intro, section_ids=[writer.intro])
    assert response.status_code == 422


def test_a_section_without_saved_versions_is_refused(writer):
    empty = _document(writer.project_id, writer.user_id, "Empty", 3)

    response = _report(writer.client, writer.project_id, scope="section", document_id=empty)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_saved_versions"


def test_section_scope_needs_a_document(writer):
    assert _report(writer.client, writer.project_id, scope="section").status_code == 422


def test_export_as_markdown_and_json(writer):
    report_id = _report(writer.client, writer.project_id, scope="section", document_id=writer.intro).json()["id"]

    markdown = writer.client.get(f"/api/projects/{writer.project_id}/provenance-reports/{report_id}?format=md")
    as_json = writer.client.get(f"/api/projects/{writer.project_id}/provenance-reports/{report_id}")

    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert "# Writing provenance report" in markdown.text
    assert as_json.json()["id"] == report_id

def test_a_report_includes_every_section_by_default():
    client, user_id = _login("sections.default@example.org")
    project_id = _project(client)
    intro = _document(project_id, user_id, "Introduction", 1)
    assert _save(client, project_id, intro, _doc(_typed("Remote work changed wages.", "s1", user_id)), 0).status_code == 201

    body = _report(client, project_id, scope="section", document_id=intro).json()

    assert sorted(body["included_sections"]) == ["ai", "limits", "pasted", "produced", "statement"]
    assert "shares" in body["report"] and "sessions" in body["report"]
    assert "ai" in body["report"] and "pasted" in body["report"] and "limits" in body["report"]
    assert body["statement_text"]
    markdown = client.get(f"/api/projects/{project_id}/provenance-reports/{body['id']}?format=md").text
    for heading in ("How the saved text was produced", "AI contributions", "Pasted and quoted material", "AI-use statement", "What this report cannot show"):
        assert heading in markdown


def test_excluded_sections_are_omitted_from_json_and_markdown():
    client, user_id = _login("sections.exclude@example.org")
    project_id = _project(client)
    intro = _document(project_id, user_id, "Introduction", 1)
    assert _save(client, project_id, intro, _doc(_typed("Remote work changed wages.", "s1", user_id)), 0).status_code == 201

    response = _report(client, project_id, scope="section", document_id=intro, sections=["produced", "limits"])
    assert response.status_code == 201, response.text
    body = response.json()

    assert sorted(body["included_sections"]) == ["limits", "produced"]
    assert "shares" in body["report"] and "sessions" in body["report"] and "limits" in body["report"]
    assert "ai" not in body["report"] and "pasted" not in body["report"]
    assert body["statement_text"] == ""

    markdown = client.get(f"/api/projects/{project_id}/provenance-reports/{body['id']}?format=md").text
    assert "How the saved text was produced" in markdown and "What this report cannot show" in markdown
    assert "AI contributions" not in markdown
    assert "Pasted and quoted material" not in markdown
    assert "AI-use statement" not in markdown

    as_json = client.get(f"/api/projects/{project_id}/provenance-reports/{body['id']}").json()
    assert sorted(as_json["included_sections"]) == ["limits", "produced"]
    assert "ai" not in as_json["report"]


def test_sections_must_be_non_empty_when_given(writer):
    response = _report(writer.client, writer.project_id, scope="section", document_id=writer.intro, sections=[])
    assert response.status_code == 422


def test_unknown_section_name_is_rejected(writer):
    response = _report(writer.client, writer.project_id, scope="section", document_id=writer.intro, sections=["not_a_section"])
    assert response.status_code == 422


def test_saving_a_covered_section_marks_its_report_stale(writer):
    client, project_id = writer.client, writer.project_id
    report_id = _report(client, project_id, scope="section", document_id=writer.intro).json()["id"]

    assert _save(client, project_id, writer.methods, _doc(_typed("Another method.", "s3", writer.actor)), 1).status_code == 201
    listed = {r["id"]: r for r in client.get(f"/api/projects/{project_id}/provenance-reports").json()}
    assert listed[report_id]["stale"] is False

    assert _save(client, project_id, writer.intro, _doc(_typed("Remote work changed wages a lot.", "s1", writer.actor)), 1).status_code == 201
    listed = {r["id"]: r for r in client.get(f"/api/projects/{project_id}/provenance-reports").json()}
    assert listed[report_id]["stale"] is True
    with SessionLocal() as db:
        artifact = db.scalar(select(Artifact).where(Artifact.kind == "provenance_report", Artifact.ref_id == report_id))
        assert artifact.status == ArtifactStatus.stale
        assert artifact.stale_reason == "section changed after the report"


def test_report_rows_cannot_be_edited(writer):
    first = _report(writer.client, writer.project_id, scope="section", document_id=writer.intro).json()["id"]
    second = _report(writer.client, writer.project_id, scope="section", document_id=writer.intro).json()["id"]

    assert first != second
    with SessionLocal() as db:
        report = db.get(ProvenanceReport, uuid.UUID(first))
        report.statement_text = "edited"
        with pytest.raises(ValueError):
            db.commit()


def test_generation_is_audited(writer):
    report_id = _report(writer.client, writer.project_id, scope="section", document_id=writer.intro).json()["id"]

    with SessionLocal() as db:
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "provenance.generated", AuditEvent.project_id == uuid.UUID(writer.project_id))
        )
        assert event is not None
        assert event.payload_json["report_id"] == report_id
