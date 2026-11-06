import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

# The CORS middleware is only added when CORS_ORIGINS is set, and it is built once at import,
# so the preflight runs in a fresh interpreter with that origin configured. cwd is a temp dir
# so that no developer .env is read.
PROBE = """
import json, os
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
out = {}
for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
    r = client.options(
        "/api/projects",
        headers={"Origin": "https://ui.example", "Access-Control-Request-Method": method},
    )
    out[method] = r.status_code
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def preflight_status(tmp_path_factory):
    env = {
        **os.environ,
        "DATABASE_URL": "sqlite+pysqlite://",
        "ENVIRONMENT": "development",
        "AUTO_CREATE_SCHEMA": "true",
        "RUN_RESEARCH_INLINE": "true",
        "SESSION_SECRET": "test-session-secret-that-is-long-enough",
        "CORS_ORIGINS": json.dumps(["https://ui.example"]),
        "PYTHONPATH": str(BACKEND),
    }
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=tmp_path_factory.mktemp("cors"),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("method", ["PUT", "PATCH"])
def test_a_cross_origin_preflight_for_a_put_or_patch_route_is_allowed(preflight_status, method):
    assert preflight_status[method] == 200


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_the_methods_that_were_already_allowed_still_pass_preflight(preflight_status, method):
    assert preflight_status[method] == 200
