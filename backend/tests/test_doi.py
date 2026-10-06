import pytest
from fastapi.testclient import TestClient

from app.agent import executor
from app.agent.arc_client import ArcRetrievedSource, _normalize
from app.config import get_settings
from app.database import SessionLocal
from app.doi import normalize_doi
from app.main import app
from app.models import Source


def _client_and_project():
    client = TestClient(app)
    assert client.post("/api/auth/development/login", json={"email": "doi@example.com", "display_name": "D"}).status_code == 200
    project = client.post("/api/projects", json={"title": "DOI project"})
    return client, project.json()["id"]


@pytest.mark.parametrize(
    "raw",
    ["10.1000/ABC.1", "https://doi.org/10.1000/ABC.1", "http://dx.doi.org/10.1000/abc.1", "doi:10.1000/abc.1", "  10.1000/abc.1 "],
)
def test_normalize_doi_forms(raw):
    assert normalize_doi(raw) == "10.1000/abc.1"


def test_normalize_doi_blank_and_invalid():
    assert normalize_doi(None) is None
    assert normalize_doi("   ") is None
    with pytest.raises(ValueError):
        normalize_doi("not-a-doi")


def test_create_source_stores_normalised_doi():
    client, project_id = _client_and_project()

    response = client.post(
        f"/api/projects/{project_id}/sources", json={"title": "Paper", "doi": "https://doi.org/10.1000/ABC"}
    )

    assert response.status_code == 201
    assert response.json()["doi"] == "10.1000/abc"
    listed = client.get(f"/api/projects/{project_id}/sources").json()
    assert listed[0]["doi"] == "10.1000/abc"


def test_create_source_rejects_malformed_doi():
    client, project_id = _client_and_project()

    response = client.post(f"/api/projects/{project_id}/sources", json={"title": "Paper", "doi": "garbage"})

    assert response.status_code == 422


def test_create_source_without_doi_still_works():
    client, project_id = _client_and_project()

    response = client.post(f"/api/projects/{project_id}/sources", json={"title": "Paper"})

    assert response.status_code == 201
    assert response.json()["doi"] is None


def test_arc_normalize_keeps_valid_doi_and_drops_malformed():
    items = _normalize(
        {
            "scholar_papers": [
                {"title": "Good", "doi": "https://doi.org/10.5555/XYZ"},
                {"title": "Bad", "doi": "nonsense"},
            ]
        }
    )

    assert [i.doi for i in items] == ["10.5555/xyz", None]


def test_arc_ingest_sets_doi_and_skips_duplicate_doi(monkeypatch):
    settings = get_settings()
    retrieved = [
        ArcRetrievedSource("A", "https://a.example/1", None, 2020, "scholar", "abs", "loc", doi="10.1/a"),
        ArcRetrievedSource("A again", "https://b.example/2", None, 2020, "scholar", "abs", "loc", doi="10.1/a"),
    ]
    monkeypatch.setattr(executor.ArcRetrievalClient, "retrieve", lambda self, topic: retrieved)
    client, project_id = _client_and_project()

    with SessionLocal() as db:
        from app.models import Project, ResearchRun
        import uuid

        project = db.get(Project, uuid.UUID(project_id))
        run = ResearchRun(project_id=project.id, question="a question long enough")
        db.add(run)
        db.flush()
        result = executor._ingest_arc_sources(db, project, run, settings)
        dois = [s.doi for s in db.query(Source).filter(Source.project_id == project.id)]

    assert result["added"] == 1
    assert dois == ["10.1/a"]
