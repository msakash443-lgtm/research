from fastapi.testclient import TestClient

from app.main import app


def development_client() -> tuple[TestClient, str]:
    client = TestClient(app)
    login = client.post(
        "/api/auth/development/login",
        json={"email": "agent@example.com", "display_name": "Agent Tester"},
    )
    assert login.status_code == 200
    project = client.post("/api/projects", json={"title": "Labour research"})
    assert project.status_code == 201
    return client, project.json()["id"]


def test_source_is_project_scoped_and_retains_auditable_excerpt():
    client, project_id = development_client()

    created = client.post(
        f"/api/projects/{project_id}/sources",
        json={
            "title": "Official statistical release",
            "url": "https://example.gov/report",
            "source_type": "official statistic",
            "year": 2024,
            "evidence_excerpt": "Female LFPR was reported as 32.1 percent in the referenced table.",
            "locator": "Table 3.2",
        },
    )

    assert created.status_code == 201
    assert created.json()["evidence_excerpt"].startswith("Female LFPR")
    listed = client.get(f"/api/projects/{project_id}/sources")
    assert listed.status_code == 200
    assert listed.json()[0]["excerpt_locator"] == "Table 3.2"
    assert "Official statistical release" in listed.json()[0]["apa_citation"]


def test_saved_article_library_can_be_searched():
    client, project_id = development_client()
    client.post(
        f"/api/projects/{project_id}/sources",
        json={
            "title": "Women and work in Kerala",
            "authors": ["Rao, A."],
            "year": 2022,
            "evidence_excerpt": "Female labour participation increased.",
        },
    )
    result = client.get(f"/api/projects/{project_id}/sources/search?q=kerala")
    assert result.status_code == 200
    assert result.json()[0]["title"] == "Women and work in Kerala"
    assert "(2022)" in result.json()[0]["apa_citation"]


def test_research_run_needs_evidence_before_configured_llm():
    client, project_id = development_client()

    no_sources = client.post(
        f"/api/projects/{project_id}/research-runs",
        json={"question": "What does the evidence say about female labour participation?"},
    )
    assert no_sources.status_code == 202
    assert no_sources.json()["status"] == "needs_sources"
    assert no_sources.json()["research_plan"]

    source = client.post(
        f"/api/projects/{project_id}/sources",
        json={
            "title": "Official release",
            "source_type": "official statistic",
            "evidence_excerpt": "The source reports an increase between the two periods.",
            "locator": "p. 12",
        },
    )
    assert source.status_code == 201
    awaiting_configuration = client.post(
        f"/api/projects/{project_id}/research-runs",
        json={"question": "What does the evidence say about female labour participation?"},
    )
    assert awaiting_configuration.status_code == 202
    assert awaiting_configuration.json()["status"] == "needs_configuration"
    assert "LLM_API_KEY" in awaiting_configuration.json()["error_message"]
