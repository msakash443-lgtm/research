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


def test_page_and_assets_are_revalidated_on_every_load():
    with TestClient(app) as client:
        for path in ("/", "/assets/styles.css", "/assets/app.js"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
        # Revalidating an unchanged asset is cheap: it answers 304 to its own ETag. (The small index page is
        # re-sent in full; its plain FileResponse route doesn't do conditional requests.)
        for path in ("/assets/styles.css", "/assets/app.js"):
            etag = client.get(path).headers["etag"]
            assert client.get(path, headers={"If-None-Match": etag}).status_code == 304
        assert "cache-control" not in client.get("/health").headers


def test_request_body_limit_rejects_oversized_payloads():
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/development/login",
            content=b"x" * (1024 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
