import io

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.main import app


def _pdf_bytes() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def test_literature_review_requires_supported_pdf_and_style():
    client = TestClient(app)
    assert client.post(
        "/api/auth/development/login",
        json={"email": "pdf@example.com", "display_name": "PDF Tester"},
    ).status_code == 200
    project = client.post("/api/projects", json={"title": "PDF review"}).json()
    project_id = project["id"]
    uploaded = client.post(
        f"/api/projects/{project_id}/literature-review/documents",
        files={"file": ("paper.pdf", io.BytesIO(_pdf_bytes()), "application/pdf")},
    )
    assert uploaded.status_code == 422
    assert "selectable text" in uploaded.json()["detail"]

    invalid = client.post(
        f"/api/projects/{project_id}/literature-review/documents",
        files={"file": ("paper.txt", io.BytesIO(b"not a pdf"), "text/plain")},
    )
    assert invalid.status_code == 415
