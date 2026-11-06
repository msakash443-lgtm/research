import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def world():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"brief-{uuid.uuid4().hex}@example.com", "display_name": "B"})
    project = client.post("/api/projects", json={"title": "Brief project"}).json()["id"]
    return client, project


BRIEF = {
    "topic": "Female labour participation in India",
    "keyword_groups": [
        {"label": "outcome", "terms": ["female labour participation", "women's employment"]},
        {"label": "place", "terms": ["India", "South Asia"]},
    ],
    "scope": "2000-2024, peer-reviewed",
    "min_citations": 5,
}


def test_brief_round_trips_and_feeds_the_query_builder(world):
    client, project = world
    assert client.get(f"/api/projects/{project}/brief").json()["exists"] is False

    saved = client.put(f"/api/projects/{project}/brief", json=BRIEF)
    assert saved.status_code == 200, saved.text
    assert client.get(f"/api/projects/{project}/brief").json()["min_citations"] == 5

    query = client.get(f"/api/projects/{project}/brief/query")
    assert query.status_code == 200, query.text
    assert query.json()["query_string"] == '("female labour participation" OR "women\'s employment") AND ("India" OR "South Asia")'
    assert query.json()["min_citations"] == 5

    # Saving again updates the one row rather than adding another.
    assert client.put(f"/api/projects/{project}/brief", json={**BRIEF, "min_citations": None}).json()["min_citations"] is None


def test_brief_refuses_terms_the_builder_refuses_and_negative_thresholds(world):
    client, project = world
    bad_term = {**BRIEF, "keyword_groups": [{"terms": ['say "hi"']}]}
    assert client.put(f"/api/projects/{project}/brief", json=bad_term).status_code == 422
    assert client.put(f"/api/projects/{project}/brief", json={**BRIEF, "min_citations": -1}).status_code == 422
    assert client.put(f"/api/projects/{project}/brief", json={**BRIEF, "topic": "   "}).status_code == 422


def test_query_is_empty_without_keyword_groups_and_brief_is_project_scoped(world):
    client, project = world
    client.put(f"/api/projects/{project}/brief", json={"topic": "Only a topic"})
    assert client.get(f"/api/projects/{project}/brief/query").json()["query_string"] == ""

    other = TestClient(app)
    other.post("/api/auth/development/login", json={"email": f"other-{uuid.uuid4().hex}@example.com", "display_name": "O"})
    assert other.get(f"/api/projects/{project}/brief").status_code in (403, 404)
    assert other.put(f"/api/projects/{project}/brief", json=BRIEF).status_code in (403, 404)
