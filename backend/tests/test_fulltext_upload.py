"""Manual full-text upload for paywalled sources (plan M2.7.2).

Open-access fetch (M2.7.1) flags a source as paywalled via `fulltext_access.status` in
`no_oa`/`no_pdf`/`link_only`. This endpoint lets a person upload the PDF directly instead: raw
bytes in the body (no multipart), size-capped (`ContentLengthLimitMiddleware`'s per-path override
plus a streaming cap in the handler itself, so a missing/false Content-Length can't bypass it),
type-checked (signature + a real `pypdf` parse — the "scan"), and stored write-once like the
automatic fetch.
"""

import io

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.config import get_settings
from app.main import app
from tests.test_fulltext import make_pdf

PDF = make_pdf([["Hello world, this is the body text of the paper."]])


def _login(email):
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": email, "display_name": "U"}).status_code == 200
    return client


def _source(client, title="Paper"):
    project_id = client.post("/api/projects", json={"title": "Upload"}).json()["id"]
    source = client.post(f"/api/projects/{project_id}/sources", json={"title": title, "evidence_excerpt": "x"}).json()
    return project_id, source["id"]


def _upload(client, project_id, source_id, data, content_type="application/pdf"):
    return client.post(
        f"/api/projects/{project_id}/sources/{source_id}/fulltext/upload",
        content=data, headers={"content-type": content_type},
    )


@pytest.fixture(autouse=True)
def _scratch_object_store(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "object_storage_root", str(tmp_path / "objects"))


def test_a_valid_pdf_is_stored_and_flagged_as_a_manual_upload():
    client = _login("upload1@example.com")
    project_id, source_id = _source(client)

    response = _upload(client, project_id, source_id, PDF)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["fulltext_path"]
    assert body["fulltext_access"]["status"] == "stored"
    assert body["fulltext_access"]["via"] == "manual_upload"
    assert body["fulltext_access"]["uploaded_by"]
    assert body["fulltext_access"]["sha256"] and body["fulltext_access"]["size"] == len(PDF)
    # Persisted, not just returned once.
    stored = client.get(f"/api/projects/{project_id}/sources").json()[0]
    assert stored["fulltext_path"] == body["fulltext_path"]


def test_the_wrong_content_type_is_refused_before_anything_is_read():
    client = _login("upload2@example.com")
    project_id, source_id = _source(client)

    response = _upload(client, project_id, source_id, PDF, content_type="application/octet-stream")

    assert response.status_code == 415
    assert client.get(f"/api/projects/{project_id}/sources").json()[0]["fulltext_path"] is None


def test_a_file_without_the_pdf_signature_is_refused():
    client = _login("upload3@example.com")
    project_id, source_id = _source(client)

    response = _upload(client, project_id, source_id, b"this is not a pdf at all")

    assert response.status_code == 422
    assert "not a PDF" in response.json()["detail"]


def test_a_password_protected_pdf_is_refused_not_stored():
    client = _login("upload4@example.com")
    project_id, source_id = _source(client)
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    writer.encrypt(user_password="secret")
    buf = io.BytesIO()
    writer.write(buf)

    response = _upload(client, project_id, source_id, buf.getvalue())

    assert response.status_code == 422
    assert "could not be read" in response.json()["detail"]
    assert client.get(f"/api/projects/{project_id}/sources").json()[0]["fulltext_path"] is None


def test_a_scanned_pdf_with_no_text_layer_is_refused():
    client = _login("upload5@example.com")
    project_id, source_id = _source(client)
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)  # no text operators: nothing to extract
    buf = io.BytesIO()
    writer.write(buf)

    response = _upload(client, project_id, source_id, buf.getvalue())

    assert response.status_code == 422


def test_a_source_that_already_has_stored_full_text_refuses_a_second_upload():
    client = _login("upload6@example.com")
    project_id, source_id = _source(client)
    assert _upload(client, project_id, source_id, PDF).status_code == 201

    response = _upload(client, project_id, source_id, PDF)

    assert response.status_code == 409


def test_an_oversized_declared_length_is_refused_by_the_middleware(monkeypatch):
    monkeypatch.setattr(get_settings(), "object_storage_max_bytes", 100)
    client = _login("upload7@example.com")
    project_id, source_id = _source(client)
    oversize = b"%PDF-1.4\n" + b"x" * 500

    response = _upload(client, project_id, source_id, oversize)

    assert response.status_code == 413


def test_an_undeclared_oversized_stream_is_cut_off_by_the_handler(monkeypatch):
    """No Content-Length (chunked body): the middleware can't check it, so the handler's own
    streaming cap must still refuse it rather than buffer it unbounded."""
    monkeypatch.setattr(get_settings(), "object_storage_max_bytes", 100)
    client = _login("upload8@example.com")
    project_id, source_id = _source(client)

    def chunks():
        yield b"%PDF-1.4\n"
        yield b"x" * 500

    response = client.post(
        f"/api/projects/{project_id}/sources/{source_id}/fulltext/upload",
        content=chunks(), headers={"content-type": "application/pdf"},
    )

    assert response.status_code == 413


def test_upload_requires_authentication():
    client = _login("upload9@example.com")
    project_id, source_id = _source(client)
    anonymous = TestClient(app)

    response = _upload(anonymous, project_id, source_id, PDF)

    assert response.status_code == 401


def test_other_users_cannot_upload_and_unknown_source_404s():
    import uuid

    owner = _login("upload10@example.com")
    project_id, source_id = _source(owner)
    intruder = _login("upload11@example.com")

    assert _upload(intruder, project_id, source_id, PDF).status_code == 404
    assert _upload(owner, project_id, uuid.uuid4(), PDF).status_code == 404
