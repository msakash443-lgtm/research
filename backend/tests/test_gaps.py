import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models import Extraction
from app.main import app


def world():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"gap-{uuid.uuid4().hex}@example.com", "display_name": "G"})
    project_id = client.post("/api/projects", json={"title": "Gaps"}).json()["id"]
    return client, project_id


def test_gap_requires_two_papers_or_matrix_evidence():
    client, project_id = world()
    response = client.post(f"/api/projects/{project_id}/gaps", json={"type": "empirical", "statement": "Missing evidence"})
    assert response.status_code == 422

    source_a = client.post(f"/api/projects/{project_id}/sources", json={"title": "A"}).json()["id"]
    source_b = client.post(f"/api/projects/{project_id}/sources", json={"title": "B"}).json()["id"]
    response = client.post(f"/api/projects/{project_id}/gaps", json={
        "type": "empirical", "statement": "Missing evidence", "supporting_paper_ids": [source_a, source_b],
        "priority": 4,
    })
    assert response.status_code == 201, response.text
    assert response.json()["future_research"] == []


def test_matrix_evidence_is_an_alternative_and_curation_is_audited():
    client, project_id = world()
    source_id = client.post(f"/api/projects/{project_id}/sources", json={"title": "Matrix paper"}).json()["id"]
    with SessionLocal() as db:
        db.add(Extraction(project_id=uuid.UUID(project_id), source_id=uuid.UUID(source_id), schema_version="default@1",
                          fields_json={"method": {"design": "Survey"}, "sample": {"population": "Teachers", "country": "UK"}},
                          evidence_spans={}, extracted_by="human", extractor="test"))
        db.commit()
    response = client.post(f"/api/projects/{project_id}/gaps", json={
        "type": "contextual", "statement": "No evidence in this cell", "matrix_cell_ids": ["method=survey|population=teachers"],
    })
    assert response.status_code == 201, response.text
    gap_id = response.json()["id"]
    response = client.patch(f"/api/projects/{project_id}/gaps/{gap_id}", json={"priority": 9, "selected": True})
    assert response.status_code == 200 and response.json()["priority"] == 9 and response.json()["selected"] is True
    assert any(event["action"] == "gap.curated" for event in client.get(f"/api/projects/{project_id}/audit").json())


def test_gap_type_is_restricted():
    client, project_id = world()
    response = client.post(f"/api/projects/{project_id}/gaps", json={
        "type": "impression", "statement": "Unsupported", "matrix_cell_ids": ["cell"],
    })
    assert response.status_code == 422


def test_future_research_requires_extraction_provenance():
    client, project_id = world()
    source_id = client.post(f"/api/projects/{project_id}/sources", json={"title": "Future paper"}).json()["id"]
    response = client.post(f"/api/projects/{project_id}/gaps", json={
        "type": "temporal", "statement": "Future work is unclear", "matrix_cell_ids": ["method=survey|population=teachers"],
        "future_research": [{"statement": "Study later", "source_id": source_id, "extraction_id": str(uuid.uuid4())}],
    })
    assert response.status_code == 422


def test_future_research_provenance_round_trips_in_gap_response():
    client, project_id = world()
    source_id = client.post(f"/api/projects/{project_id}/sources", json={"title": "Future paper"}).json()["id"]
    extraction_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Extraction(id=extraction_id, project_id=uuid.UUID(project_id), source_id=uuid.UUID(source_id), schema_version="default@1",
                          fields_json={"method": {"design": "Survey"}, "sample": {"population": "Teachers", "country": "UK"}},
                          evidence_spans={}, extracted_by="human", extractor="test"))
        db.commit()
    response = client.post(f"/api/projects/{project_id}/gaps", json={
        "type": "temporal", "statement": "Future work is unclear", "matrix_cell_ids": ["method=survey|population=teachers"],
        "future_research": [{"statement": "Study later", "source_id": source_id, "extraction_id": str(extraction_id)}],
    })
    assert response.status_code == 201, response.text
    assert response.json()["future_research"] == [{"statement": "Study later", "source_id": source_id, "extraction_id": str(extraction_id)}]


def test_gap_rejects_cross_project_papers_and_unknown_matrix_cells():
    client, project_id = world()
    other_project = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    other_source = client.post(f"/api/projects/{other_project}/sources", json={"title": "Other paper"}).json()["id"]
    response = client.post(f"/api/projects/{project_id}/gaps", json={
        "type": "empirical", "statement": "Cross project", "supporting_paper_ids": [other_source, str(uuid.uuid4())],
    })
    assert response.status_code == 422
    response = client.post(f"/api/projects/{project_id}/gaps", json={
        "type": "empirical", "statement": "Unknown cell", "matrix_cell_ids": ["method=missing|population=missing"],
    })
    assert response.status_code == 422
