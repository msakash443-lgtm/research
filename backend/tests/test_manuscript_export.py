"""Manuscript export (X.31.13): Markdown, DOCX, JSON and BibTeX of the latest saved sections."""

import io
import json
import uuid
from types import SimpleNamespace

import pytest
from docx import Document as WordDocument
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import manuscript_export as me
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Document, DocumentVersion, Source


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "D"}).json()["id"]
    return client, user_id


def _prov(sid, user_id):
    return {"type": "prov", "attrs": {"sid": sid, "origin": "typed", "by": user_id, "sug": None, "note": None, "paste": None, "src": None}}


def _text(text, sid, user_id, *extra):
    return {"type": "text", "text": text, "marks": [_prov(sid, user_id), *extra]}


def _cite(source_id):
    return {"type": "cite", "attrs": {"source_id": source_id}}


def _new_document(project_id, user_id, title, position):
    with SessionLocal() as db:
        document = Document(project_id=uuid.UUID(project_id), title=title, position=position, created_by=f"user:{user_id}")
        db.add(document)
        db.commit()
        return str(document.id)


def _save(client, project_id, document_id, content):
    response = client.post(
        f"/api/projects/{project_id}/documents/{document_id}/versions",
        json={"base_number": 0, "client_save_id": uuid.uuid4().hex, "client_session_id": "s", "content_json": content},
    )
    assert response.status_code == 201, response.text


def _source(project_id, **fields):
    with SessionLocal() as db:
        row = Source(project_id=uuid.UUID(project_id), **fields)
        db.add(row)
        db.commit()
        return str(row.id)


@pytest.fixture
def world():
    client, user_id = _login("writer.export@example.org")
    project_id = client.post("/api/projects", json={"title": "Remote Work"}).json()["id"]
    verified = _source(
        project_id, title="Wages after remote work", authors=["Smith, Jane", "Lee, Ann"], year=2021,
        venue="Labour Journal", doi="10.1/abc", metadata_verified=True,
    )
    unverified = _source(project_id, title="Unchecked claim", authors=["Doe, Joe"], year=2020, metadata_verified=True)
    intro = _new_document(project_id, user_id, "Introduction", 1)
    methods = _new_document(project_id, user_id, "Methods", 2)
    _new_document(project_id, user_id, "Never saved", 3)
    _save(client, project_id, intro, {"type": "doc", "content": [
        {"type": "heading", "attrs": {"level": 2}, "content": [_text("Background", "h", user_id)]},
        {"type": "paragraph", "content": [
            _text("Wages ", "a", user_id), _text("rose", "b", user_id, {"type": "strong"}), _text(" after ", "c", user_id),
            _cite(verified), _text(" and fell ", "d", user_id, {"type": "em"}), _cite(unverified),
        ]},
        {"type": "bullet_list", "content": [
            {"type": "list_item", "content": [{"type": "paragraph", "content": [_text("first point", "e", user_id)]}]},
            {"type": "list_item", "content": [{"type": "paragraph", "content": [_text("second point", "f", user_id)]}]},
        ]},
    ]})
    _save(client, project_id, methods, {"type": "doc", "content": [
        {"type": "paragraph", "content": [_text("We used panel data.", "g", user_id)]},
    ]})
    # Verification can be withdrawn after a section was saved; the save API only accepts verified citations.
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(unverified)).metadata_verified = False
        db.commit()
    return SimpleNamespace(client=client, project_id=project_id, user_id=user_id, verified=verified, unverified=unverified)


def _export(world, fmt):
    return world.client.get(f"/api/projects/{world.project_id}/manuscript/export?format={fmt}")


def test_markdown_has_sections_in_order_formatting_and_references(world):
    response = _export(world, "md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert 'filename="manuscript.md"' in response.headers["content-disposition"]
    text = response.text
    assert text.startswith("# Remote Work\n")
    assert text.index("## Introduction") < text.index("## Methods")
    assert "### Background" in text
    assert "Wages **rose** after (Smith 2021) *and fell* [unverified source]" in text
    assert "- first point" in text and "- second point" in text
    assert "## References" in text
    assert "Smith, Jane; Lee, Ann (2021). Wages after remote work. Labour Journal. https://doi.org/10.1/abc" in text
    assert "Unchecked claim" not in text.split("## References")[1]
    assert "Never saved" not in text
    assert response.headers["x-unsaved-sections"] == "1"


def test_authorship_marks_do_not_appear_in_the_export(world):
    text = _export(world, "md").text

    assert "prov" not in text and world.user_id not in text


def test_docx_opens_and_carries_headings_runs_and_references(world):
    response = _export(world, "docx")

    assert response.status_code == 200
    assert "wordprocessingml" in response.headers["content-type"]
    word = WordDocument(io.BytesIO(response.content))
    texts = [p.text for p in word.paragraphs]
    assert texts[0] == "Remote Work"
    assert "Introduction" in texts and "Methods" in texts and "References" in texts
    body = next(p for p in word.paragraphs if p.text.startswith("Wages"))
    assert body.text == "Wages rose after (Smith 2021) and fell [unverified source]"
    assert [r.text for r in body.runs if r.bold] == ["rose"]
    assert [r.text for r in body.runs if r.italic] == [" and fell "]
    assert any(p.style.name == "List Bullet" and p.text == "first point" for p in word.paragraphs)


def test_json_lists_saved_sections_and_verified_references(world):
    body = json.loads(_export(world, "json").text)

    assert [s["title"] for s in body["sections"]] == ["Introduction", "Methods"]
    assert body["sections"][0]["version"] == 1
    assert body["sections"][0]["content_json"]["type"] == "doc"
    assert [r["id"] for r in body["references"]] == [world.verified]


def test_bibtex_has_only_cited_verified_sources(world):
    _source(world.project_id, title="Verified but never cited", authors=["Roe, Rae"], year=2019, metadata_verified=True)

    response = _export(world, "bibtex")

    assert response.status_code == 200
    assert 'filename="manuscript.bib"' in response.headers["content-disposition"]
    assert "Wages after remote work" in response.text
    assert "Unchecked claim" not in response.text
    assert "never cited" not in response.text


def test_nothing_saved_exports_only_the_title(world):
    client, user_id = _login("empty.export@example.org")
    project_id = client.post("/api/projects", json={"title": "Empty"}).json()["id"]
    _new_document(project_id, user_id, "Draft", 1)

    response = client.get(f"/api/projects/{project_id}/manuscript/export?format=md")

    assert response.status_code == 200
    assert response.text == "# Empty\n"
    assert response.headers["x-unsaved-sections"] == "1"


def test_unknown_format_is_rejected(world):
    assert _export(world, "pdf").status_code == 422


def test_export_is_audited_with_format_and_counts(world):
    _export(world, "docx")

    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "manuscript.exported", AuditEvent.project_id == uuid.UUID(world.project_id))).all()
    assert [e.payload_json for e in events] == [{"format": "docx", "sections": 2, "references": 1, "unsaved_sections": 1}]


def test_a_citation_to_a_source_in_another_project_is_marked_unknown(world):
    other_client, _ = _login("other.export@example.org")
    other_project = other_client.post("/api/projects", json={"title": "Other"}).json()["id"]
    foreign = _source(other_project, title="Foreign", authors=["Fox, Al"], year=2018, metadata_verified=True)
    doc = _new_document(world.project_id, world.user_id, "Discussion", 4)
    content = {"type": "doc", "content": [{"type": "paragraph", "content": [_text("See ", "x", world.user_id), _cite(foreign)]}]}
    with SessionLocal() as db:  # the save API refuses this citation, so the version is written directly
        db.add(DocumentVersion(document_id=uuid.UUID(doc), project_id=uuid.UUID(world.project_id), number=1, content_json=content,
                               content_text="See", content_hash="0" * 64, saved_by=world.user_id, client_save_id="foreign"))
        db.commit()

    text = _export(world, "md").text

    assert "See [unknown source]" in text
    assert "Foreign" not in text


def test_pure_labels():
    source = me.CitedSource("1", "T", ("Jane Smith",), None, None, None, True)
    assert me.citation_label(source) == "(Smith)"
    assert me.citation_label(None) == me.UNKNOWN_LABEL
