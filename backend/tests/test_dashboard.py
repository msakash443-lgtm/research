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


def test_research_type_picker_radios_are_not_stretched_to_the_full_row_width():
    # The global `input { width:100% }` would put each radio on its own line above the type name.
    with TestClient(app) as client:
        css = client.get("/assets/styles.css").text
    rule = next(line for line in css.splitlines() if line.startswith(".panel .type-option input"))
    assert "width:auto" in rule


def test_narrow_suggestion_cards_stack_provenance_labels_above_values():
    with TestClient(app) as client:
        css = client.get("/assets/styles.css").text
    assert "@container suggestion-card (max-width:280px)" in css
    assert ".suggestion-card .provenance-badge { grid-template-columns:minmax(0,1fr)" in css

def test_request_body_limit_rejects_oversized_payloads():
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/development/login",
            content=b"x" * (1024 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
