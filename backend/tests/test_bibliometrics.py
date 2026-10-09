import uuid

from fastapi.testclient import TestClient

from app.bibliometrics import publication_trends
from app.database import SessionLocal
from app.main import app
from app.models import Source


def test_publication_trends_are_sorted_and_report_unknown_years():
    result = publication_trends([2022, None, 2020, 2022, None])

    assert result == {
        "by_year": [{"year": 2020, "count": 1}, {"year": 2022, "count": 2}],
        "total_sources": 5,
        "unknown_year": 2,
        "screening_filter": "none",
        "note": "Includes all non-merged sources regardless of screening status; sources without a year are counted as unknown.",
    }


def test_publication_trends_of_an_empty_project_have_no_years():
    assert publication_trends([])["by_year"] == []
    assert publication_trends([])["unknown_year"] == 0


def test_publication_trends_api_counts_active_sources_regardless_of_screening_state():
    tag = uuid.uuid4().hex[:8]
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"bib-{tag}@example.com", "display_name": "B"})
    project_id = client.post("/api/projects", json={"title": "Bibliometrics"}).json()["id"]

    for title, year in (("Older", 2019), ("Newer A", 2022), ("Newer B", 2022), ("Unknown", None), ("Merged", 2020)):
        body = {"title": title}
        if year is not None:
            body["year"] = year
        response = client.post(f"/api/projects/{project_id}/sources", json=body)
        assert response.status_code == 201, response.text
        if title == "Merged":
            merged_id = uuid.UUID(response.json()["id"])

    with SessionLocal() as db:
        merged = db.get(Source, merged_id)
        merged.merged_into = uuid.uuid4()
        db.commit()

    response = client.get(f"/api/projects/{project_id}/bibliometrics/publication-trends")

    assert response.status_code == 200, response.text
    assert response.json()["by_year"] == [{"year": 2019, "count": 1}, {"year": 2022, "count": 2}]
    assert response.json()["total_sources"] == 4
    assert response.json()["unknown_year"] == 1
    assert response.json()["screening_filter"] == "none"
