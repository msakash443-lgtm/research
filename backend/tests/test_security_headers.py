from fastapi.testclient import TestClient

from app import main

# X.4.2: Strict-Transport-Security tells browsers to use HTTPS only. It is sent in production (which
# validates PUBLIC_ORIGIN as https) and never in development, where the local server is plain http.

HSTS = "max-age=31536000; includeSubDomains"


def test_hsts_is_sent_in_production(monkeypatch):
    monkeypatch.setattr(main.settings, "environment", "production")
    response = TestClient(main.app).get("/health")
    assert response.headers["Strict-Transport-Security"] == HSTS


def test_hsts_is_not_sent_in_development(monkeypatch):
    monkeypatch.setattr(main.settings, "environment", "development")
    response = TestClient(main.app).get("/health")
    assert "Strict-Transport-Security" not in response.headers
    # The other headers still apply in development.
    assert response.headers["X-Frame-Options"] == "DENY"
