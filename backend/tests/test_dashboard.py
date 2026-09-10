from fastapi.testclient import TestClient

from app.main import app


def test_dashboard_development_login_can_create_a_project():
    with TestClient(app) as client:
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Research AI" in dashboard.text

        login = client.post(
            "/api/auth/development/login",
            json={"email": "dashboard@example.com", "display_name": "Dashboard User"},
        )
        assert login.status_code == 200

        project = client.post("/api/projects", json={"title": "A usable project"})
        assert project.status_code == 201
        assert project.json()["title"] == "A usable project"


def test_request_body_limit_rejects_oversized_payloads():
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/development/login",
            content=b"x" * (1024 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
