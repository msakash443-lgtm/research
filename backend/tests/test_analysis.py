import io

from fastapi.testclient import TestClient

from app.main import app


def client_and_project() -> tuple[TestClient, str]:
    client = TestClient(app)
    assert client.post(
        "/api/auth/development/login",
        json={"email": "analysis@example.com", "display_name": "Analysis Tester"},
    ).status_code == 200
    project = client.post("/api/projects", json={"title": "Analysis"}).json()
    return client, project["id"]


def upload(client: TestClient, project_id: str) -> str:
    csv = b"wage,gender,education,experience\n10,0,12,2\n12,0,14,4\n14,0,15,5\n20,1,16,3\n22,1,17,5\n24,1,18,6\n"
    response = client.post(
        f"/api/projects/{project_id}/analysis/datasets",
        files={"file": ("wages.csv", io.BytesIO(csv), "text/csv")},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_prompt_analysis_returns_descriptive_statistics():
    client, project_id = client_and_project()
    dataset_id = upload(client, project_id)
    response = client.post(
        f"/api/projects/{project_id}/analysis/runs",
        json={"dataset_id": dataset_id, "prompt": "Calculate mean and median for wage."},
    )
    assert response.status_code == 200
    assert response.json()["result"]["statistics"]["wage"]["mean"] == 17.0


def test_oaxaca_blinder_prompt_runs_in_backend():
    client, project_id = client_and_project()
    dataset_id = upload(client, project_id)
    response = client.post(
        f"/api/projects/{project_id}/analysis/runs",
        json={
            "dataset_id": dataset_id,
            "prompt": "Run Oaxaca-Blinder with outcome=wage; group=gender; predictors=education,experience.",
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["operation"] == "oaxaca_blinder"
    assert "explained" in result
    assert "unexplained" in result
