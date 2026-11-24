"""Uploaded research documents (plan M8.1): PDF and DOCX uploads for the research-figures work.

Behind `Settings.research_figures_enabled`, which is off by default: with it off every route is a 404.
"""

import hashlib
import io

import pytest
from docx import Document as DocxDocument

from app.config import get_settings
from app.object_storage import get_object_store
from app.uploaded_documents import PDF_MEDIA_TYPE, extract_text, format_for
from tests.api_helpers import create_project, sign_in
from tests.test_fulltext import make_pdf

PDF = make_pdf([["Uploaded research document body text."]])
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx_bytes() -> bytes:
    buffer = io.BytesIO()
    doc = DocxDocument()
    doc.add_heading("Method", level=1)
    doc.add_paragraph("We surveyed 40 clinics.")
    doc.save(buffer)
    return buffer.getvalue()


def _upload(client, project_id, data, content_type="application/pdf", filename="paper.pdf"):
    return client.post(
        f"/api/projects/{project_id}/uploaded-documents/upload",
        params={"filename": filename}, content=data, headers={"content-type": content_type},
    )


@pytest.fixture(autouse=True)
def _scratch_object_store_and_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "object_storage_root", str(tmp_path / "objects"))
    monkeypatch.setattr(get_settings(), "research_figures_enabled", True)


def test_a_valid_pdf_is_stored_with_its_checksum_size_and_filename():
    client = sign_in("uploaded-pdf@example.com")
    project_id = create_project(client, "Uploads")

    response = _upload(client, project_id, PDF, filename="  Smith 2020.pdf  ")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["filename"] == "Smith 2020.pdf"
    assert body["media_type"] == "application/pdf"
    assert body["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert body["size"] == len(PDF)
    assert body["withdrawn_at"] is None
    stored = get_object_store(get_settings()).get(f"projects/{project_id}/uploaded-documents/{body['id']}/original.pdf")
    assert stored == PDF


def test_a_valid_docx_is_accepted_and_stored_as_docx():
    client = sign_in("uploaded-docx@example.com")
    project_id = create_project(client, "Uploads")
    data = _docx_bytes()

    response = _upload(client, project_id, data, content_type=DOCX_MEDIA_TYPE, filename="method.docx")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["media_type"] == DOCX_MEDIA_TYPE
    assert body["sha256"] == hashlib.sha256(data).hexdigest()
    stored = get_object_store(get_settings()).get(f"projects/{project_id}/uploaded-documents/{body['id']}/original.docx")
    assert stored == data


def test_an_upload_records_an_audit_event_for_the_creation():
    client = sign_in("uploaded-audit@example.com")
    project_id = create_project(client, "Uploads")

    created = _upload(client, project_id, PDF).json()

    actions = [event["action"] for event in client.get(f"/api/projects/{project_id}/audit").json()]
    assert "uploaded_document.created" in actions
    assert created["id"]


def test_a_text_plain_body_is_refused_with_415():
    client = sign_in("uploaded-text@example.com")
    project_id = create_project(client, "Uploads")

    response = _upload(client, project_id, b"just some text", content_type="text/plain", filename="notes.txt")

    assert response.status_code == 415
    assert client.get(f"/api/projects/{project_id}/uploaded-documents").json() == []


def test_a_body_over_the_size_cap_is_refused_with_413(monkeypatch):
    client = sign_in("uploaded-big@example.com")
    project_id = create_project(client, "Uploads")
    monkeypatch.setattr(get_settings(), "object_storage_max_bytes", 64)

    response = _upload(client, project_id, PDF)

    assert response.status_code == 413
    assert client.get(f"/api/projects/{project_id}/uploaded-documents").json() == []


def test_bytes_that_are_not_the_declared_format_are_refused_with_422():
    client = sign_in("uploaded-magic@example.com")
    project_id = create_project(client, "Uploads")

    response = _upload(client, project_id, b"this is plain text, not a PDF")

    assert response.status_code == 422
    assert "PDF" in response.json()["detail"]
    assert client.get(f"/api/projects/{project_id}/uploaded-documents").json() == []


def test_a_blank_filename_is_refused_with_422():
    client = sign_in("uploaded-blank@example.com")
    project_id = create_project(client, "Uploads")

    response = _upload(client, project_id, PDF, filename="   ")

    assert response.status_code == 422


def test_with_the_flag_off_every_uploaded_document_route_returns_404(monkeypatch):
    client = sign_in("uploaded-off@example.com")
    project_id = create_project(client, "Uploads")
    monkeypatch.setattr(get_settings(), "research_figures_enabled", False)

    assert _upload(client, project_id, PDF).status_code == 404
    assert client.get(f"/api/projects/{project_id}/uploaded-documents").status_code == 404
    assert client.get(f"/api/projects/{project_id}/uploaded-documents/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.post(f"/api/projects/{project_id}/uploaded-documents/00000000-0000-0000-0000-000000000000/withdraw").status_code == 404


def test_withdrawing_keeps_the_row_and_its_bytes_and_a_second_withdrawal_is_refused():
    client = sign_in("uploaded-withdraw@example.com")
    project_id = create_project(client, "Uploads")
    created = _upload(client, project_id, PDF).json()

    first = client.post(f"/api/projects/{project_id}/uploaded-documents/{created['id']}/withdraw")
    second = client.post(f"/api/projects/{project_id}/uploaded-documents/{created['id']}/withdraw")

    assert first.status_code == 200, first.text
    assert first.json()["withdrawn_at"] is not None
    assert second.status_code == 409
    listed = client.get(f"/api/projects/{project_id}/uploaded-documents").json()
    assert [row["id"] for row in listed] == [created["id"]]
    assert get_object_store(get_settings()).get(f"projects/{project_id}/uploaded-documents/{created['id']}/original.pdf") == PDF


def test_an_upload_from_one_project_is_not_visible_from_another():
    client = sign_in("uploaded-isolation@example.com")
    first_project = create_project(client, "First")
    second_project = create_project(client, "Second")
    created = _upload(client, first_project, PDF).json()

    assert client.get(f"/api/projects/{second_project}/uploaded-documents/{created['id']}").status_code == 404
    assert client.post(f"/api/projects/{second_project}/uploaded-documents/{created['id']}/withdraw").status_code == 404


def test_a_pdf_with_no_text_layer_is_refused_with_code_no_extractable_text():
    client = sign_in("uploaded-scan@example.com")
    project_id = create_project(client, "Uploads")

    response = _upload(client, project_id, make_pdf([[]]))

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "no_extractable_text"
    assert client.get(f"/api/projects/{project_id}/uploaded-documents").json() == []


def test_a_docx_whose_body_is_unreadable_after_its_signature_is_refused_with_code_unreadable():
    client = sign_in("uploaded-corrupt-docx@example.com")
    project_id = create_project(client, "Uploads")

    response = _upload(client, project_id, b"PK\x03\x04 this is not a zip archive", content_type=DOCX_MEDIA_TYPE, filename="broken.docx")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unreadable"


def test_a_docx_with_no_text_in_its_body_is_refused_with_code_no_extractable_text():
    buffer = io.BytesIO()
    empty = DocxDocument()
    empty.add_paragraph("")
    empty.save(buffer)
    client = sign_in("uploaded-blank-docx@example.com")
    project_id = create_project(client, "Uploads")

    response = _upload(client, project_id, buffer.getvalue(), content_type=DOCX_MEDIA_TYPE, filename="blank.docx")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "no_extractable_text"


def test_the_content_route_returns_pdf_text_with_page_anchors_and_the_stored_checksum():
    client = sign_in("uploaded-content-pdf@example.com")
    project_id = create_project(client, "Uploads")
    created = _upload(client, project_id, PDF).json()

    body = client.get(f"/api/projects/{project_id}/uploaded-documents/{created['id']}/content").json()

    assert "Uploaded research document body text." in body["text"]
    assert [page["number"] for page in body["pages"]] == [1]
    assert body["sections"] == []
    assert body["sha256"] == hashlib.sha256(PDF).hexdigest()


def test_the_content_route_returns_docx_headings_as_sections_with_their_offsets():
    buffer = io.BytesIO()
    doc = DocxDocument()
    doc.add_heading("Method", level=1)
    doc.add_paragraph("We surveyed 40 clinics.")
    doc.add_heading("Participants", level=2)
    doc.save(buffer)
    data = buffer.getvalue()
    client = sign_in("uploaded-content-docx@example.com")
    project_id = create_project(client, "Uploads")
    created = _upload(client, project_id, data, content_type=DOCX_MEDIA_TYPE, filename="method.docx").json()

    body = client.get(f"/api/projects/{project_id}/uploaded-documents/{created['id']}/content").json()

    assert [section["title"] for section in body["sections"]] == ["Method", "Participants"]
    assert [section["name"] for section in body["sections"]] == ["methods", "participants"]
    assert [section["page"] for section in body["sections"]] == [0, 0]
    assert all(body["text"][section["start"]:].startswith(section["title"]) for section in body["sections"])
    assert "We surveyed 40 clinics." in body["text"]
    assert body["pages"] == []


def test_the_content_route_refuses_a_withdrawn_document_with_409():
    client = sign_in("uploaded-content-withdrawn@example.com")
    project_id = create_project(client, "Uploads")
    created = _upload(client, project_id, PDF).json()
    assert client.post(f"/api/projects/{project_id}/uploaded-documents/{created['id']}/withdraw").status_code == 200

    response = client.get(f"/api/projects/{project_id}/uploaded-documents/{created['id']}/content")

    assert response.status_code == 409


def test_the_content_route_is_a_404_when_the_figures_flag_is_off(monkeypatch):
    client = sign_in("uploaded-content-flag@example.com")
    project_id = create_project(client, "Uploads")
    created = _upload(client, project_id, PDF).json()
    monkeypatch.setattr(get_settings(), "research_figures_enabled", False)

    assert client.get(f"/api/projects/{project_id}/uploaded-documents/{created['id']}/content").status_code == 404


def test_a_quote_from_an_uploaded_pdf_is_located_by_find_quote():
    text = extract_text(format_for(PDF_MEDIA_TYPE), PDF, max_pdf_pages=100, max_docx_paragraphs=100)

    assert len(text.find_quote("Uploaded research document body text.")) == 1
