import csv
import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Extraction, Source
from tests.test_fulltext import make_pdf


def _world():
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    client.post("/api/auth/development/login", json={"email": f"evidence-{tag}@example.com", "display_name": "E"})
    project = client.post("/api/projects", json={"title": "Evidence"}).json()["id"]
    source = client.post(f"/api/projects/{project}/sources", json={"title": "One paper"}).json()["id"]
    return client, project, source


def _row(project, source, value, *, verified=False, hours=0, version="default@1"):
    when = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(hours=hours)
    row_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Extraction(id=row_id, project_id=uuid.UUID(project), source_id=uuid.UUID(source),
                          schema_version=version, fields_json={"research_question": value},
                          evidence_spans={"research_question": {"quote": "We ask why", "page": 1}} if value else {},
                          verified_by_human=verified, verified_by="tester" if verified else None,
                          verified_at=when if verified else None, extracted_by="human", extractor="tester",
                          created_at=when, updated_at=when))
        db.commit()
    return str(row_id)


def test_matrix_chooses_verified_record_and_keeps_schema_versions_separate():
    client, project, source = _world()
    selected = _row(project, source, "Verified", verified=True)
    _row(project, source, "Newer", hours=1)
    _row(project, source, "Old schema", hours=2, version="default@2")
    result = client.get(f"/api/projects/{project}/evidence-matrix")
    assert result.status_code == 200
    data = result.json()
    assert data["schema_version"] == "default@1"
    assert len(data["papers"]) == 1
    assert data["papers"][0]["extraction_id"] == selected
    cell = data["papers"][0]["cells"]["research_question"]
    assert (cell["value"], cell["status"], cell["quote"], cell["page"]) == ("Verified", "checked", "We ask why", 1)
    assert cell["source_text_url"].endswith(f"/{selected}/fields/research_question/source-text")


def test_unextracted_sources_remain_visible_with_distinct_cell_status():
    client, project, source = _world()
    data = client.get(f"/api/projects/{project}/evidence-matrix").json()
    assert len(data["papers"]) == 1
    paper = data["papers"][0]
    assert paper["source_id"] == source and paper["extraction_id"] is None
    assert paper["cells"]["research_question"]["status"] == "not_extracted"
    assert paper["cells"]["research_question"]["source_text_url"] is None


def test_cell_opens_stored_source_text_and_refuses_missing_or_foreign_cells(monkeypatch):
    client, project, source = _world()
    row_id = _row(project, source, "Why?")
    from app.routers import evidence_matrix as routes
    monkeypatch.setattr(routes, "get_object_store", lambda settings: type("Store", (), {
        "get": lambda self, key: make_pdf([["Introduction", "We ask why and measure outcomes."]])
    })())
    with SessionLocal() as db:
        db.get(Source, uuid.UUID(source)).fulltext_path = "stored-pdf-key"
        db.commit()
    url = f"/api/projects/{project}/evidence-matrix/{row_id}/fields/research_question/source-text"
    result = client.get(url)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["text"][data["highlight_start"]:data["highlight_end"]] == "We ask why"
    assert data["page"] == [1]
    assert client.get(url.replace("research_question", "unknown_field")).status_code == 404
    other = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    assert client.get(url.replace(project, other)).status_code == 404


def test_exports_include_provenance_and_escape_spreadsheet_formulas():
    client, project, source = _world()
    _row(project, source, "=HYPERLINK(\"bad\")")
    base = f"/api/projects/{project}/evidence-matrix"
    csv_response = client.get(base + "/export.csv")
    assert csv_response.status_code == 200
    parsed = list(csv.reader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    key = parsed[0].index("research_question_value")
    assert parsed[1][key].startswith("'=HYPERLINK")
    assert "research_question_quote" in parsed[0] and "research_question_page" in parsed[0]
    excel_response = client.get(base + "/export.xlsx")
    assert excel_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(excel_response.content)) as archive:
        tree = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        assert all(cell.attrib.get("t") == "inlineStr" for cell in tree.iter() if cell.tag.endswith("}c"))
        assert any("'=HYPERLINK" in node.text for node in tree.iter() if node.text)


def test_xlsx_removes_xml_forbidden_controls_from_valid_text():
    client, project, source = _world()
    _row(project, source, "A\x0bB\x0cC")
    response = client.get(f"/api/projects/{project}/evidence-matrix/export.xlsx")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        tree = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        assert any(node.text == "ABC" for node in tree.iter() if node.text)
