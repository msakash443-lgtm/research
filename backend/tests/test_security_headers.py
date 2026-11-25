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


def test_microphone_policy_allows_same_origin_capture_only():
    response = TestClient(main.app).get("/health")
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(self), geolocation=()"


def test_offline_shell_assets_are_served():
    client = TestClient(main.app)
    assert client.get("/sw.js").status_code == 200
    assert "outbox-sync" in client.get("/sw.js").text
    assert client.get("/manifest.webmanifest").json()["start_url"] == "/"


def test_production_serves_no_api_schema_or_docs(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    from tests.test_environment_settings import PRODUCTION

    probe = (
        "import json\n"
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "client = TestClient(app, base_url='https://lab.example.org')\n"
        "print(json.dumps({p: client.get(p).status_code for p in ('/openapi.json', '/docs', '/redoc')}))\n"
    )
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("ENVIRONMENT", "DATABASE_URL"))},
        "ENVIRONMENT": "production",
        "SESSION_SECRET": PRODUCTION["session_secret"],
        "DATABASE_URL": PRODUCTION["database_url"],
        "AUTO_CREATE_SCHEMA": "false",
        "PUBLIC_ORIGIN": PRODUCTION["public_origin"],
        "ALLOWED_HOSTS": json.dumps(["lab.example.org"]),
        "GOOGLE_CLIENT_ID": PRODUCTION["google_client_id"],
        "GOOGLE_CLIENT_SECRET": PRODUCTION["google_client_secret"],
        "GOOGLE_REDIRECT_URI": PRODUCTION["google_redirect_uri"],
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    result = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"/openapi.json": 404, "/docs": 404, "/redoc": 404}


def test_every_page_and_api_response_carries_the_strict_csp():
    client = TestClient(main.app)
    for path in ("/", "/health"):
        csp = client.get(path).headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "unsafe-inline" not in csp
