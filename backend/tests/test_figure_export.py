"""Figure SVG export (plan M8.8): a clean version downloads as an SVG that carries its provenance and is audited; a version
with deterministic errors is refused with its findings; a renderer that cannot start answers 503 with a code."""

import uuid
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import select

from app import figure_store
from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent, Figure, UploadedDocument
from tests.api_helpers import create_project, sign_in
from tests.test_fulltext import make_pdf

SVG_NS = "{http://www.w3.org/2000/svg}"
PDF = make_pdf([["Trust predicts collaboration among partners. Methods use a survey."]])
SPEC = {
    "nodes": [
        {"id": "trust", "label": "Trust", "role": "independent", "evidence": [{"quote": "Trust predicts collaboration", "section": None}]},
        {"id": "collab", "label": "Collaboration", "role": "dependent", "evidence": []},
    ],
    "edges": [{"from": "trust", "to": "collab", "kind": "direct", "label": "predicts", "evidence": []}],
}
DANGLING = {
    "nodes": [SPEC["nodes"][0]],
    "edges": [{"from": "trust", "to": "collab", "kind": "direct", "label": "predicts", "evidence": []}],
}


@pytest.fixture(autouse=True)
def _scratch_object_store_and_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "object_storage_root", str(tmp_path / "objects"))
    monkeypatch.setattr(get_settings(), "research_figures_enabled", True)


def _export_project(email):
    client = sign_in(email)
    pid = create_project(client, "Figure Export")
    upload = client.post(
        f"/api/projects/{pid}/uploaded-documents/upload?filename=paper.pdf",
        content=PDF, headers={"content-type": "application/pdf"},
    )
    assert upload.status_code == 201, upload.text
    return client, pid, upload.json()["id"]


def _export_figure(pid, upload_id, spec):
    with SessionLocal() as db:
        figure = Figure(
            id=uuid.uuid4(), project_id=uuid.UUID(pid), uploaded_document_id=uuid.UUID(upload_id),
            kind="conceptual_framework", title="Framework", created_by="test",
        )
        db.add(figure)
        db.flush()
        figure_store.write_version(db, figure, spec, origin=figure_store.ORIGIN_HUMAN_EDITED, created_by="test")
        db.commit()
        return str(figure.id)


def _export_document_sha(upload_id):
    with SessionLocal() as db:
        return db.get(UploadedDocument, uuid.UUID(upload_id)).sha256


def _exported_events(pid):
    with SessionLocal() as db:
        return db.scalars(
            select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(pid), AuditEvent.action == "figure.exported")
        ).all()


@pytest.mark.requires_graphviz
def test_a_clean_version_exports_as_a_download_carrying_its_provenance_and_is_audited():
    client, pid, upload_id = _export_project("figures-export-ok@example.com")
    figure_id = _export_figure(pid, upload_id, SPEC)

    response = client.get(f"/api/projects/{pid}/figures/{figure_id}/versions/1/export.svg")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert b"<script" not in response.content.lower()
    root = ET.fromstring(response.content)
    metadata = {child.tag.removeprefix(SVG_NS): child.text or "" for child in root.find(f"{SVG_NS}metadata")}
    assert metadata["figure_id"] == figure_id
    assert metadata["version"] == "1"
    assert metadata["origin"] == "human_edited"
    assert metadata["document_sha256"] == _export_document_sha(upload_id)
    assert metadata["exported_at"]
    [event] = _exported_events(pid)
    assert event.payload_json["figure_id"] == figure_id
    assert event.payload_json["number"] == 1


def test_a_version_with_an_error_finding_is_refused_and_its_findings_are_listed():
    client, pid, upload_id = _export_project("figures-export-errors@example.com")
    figure_id = _export_figure(pid, upload_id, DANGLING)

    response = client.get(f"/api/projects/{pid}/figures/{figure_id}/versions/1/export.svg")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "figure_has_errors"
    assert "dangling_edge" in [finding["code"] for finding in detail["findings"]]
    assert _exported_events(pid) == []


def test_a_clean_version_export_answers_503_when_the_renderer_cannot_start(monkeypatch):
    monkeypatch.setattr(get_settings(), "graphviz_dot_path", "no-such-dot-binary")
    client, pid, upload_id = _export_project("figures-export-503@example.com")
    figure_id = _export_figure(pid, upload_id, SPEC)

    response = client.get(f"/api/projects/{pid}/figures/{figure_id}/versions/1/export.svg")

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "figure_renderer_unavailable"
    assert _exported_events(pid) == []
