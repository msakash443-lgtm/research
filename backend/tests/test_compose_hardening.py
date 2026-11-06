from pathlib import Path

import yaml

# X.4.4: the dev compose file must not expose Postgres beyond this machine, and must not fall back to a
# known password. Docker isn't available in the test environment, so this checks the file itself.

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_postgres_is_published_on_loopback_only():
    ports = _compose()["services"]["db"]["ports"]
    assert ports == ["127.0.0.1:5432:5432"]


def test_password_has_no_known_fallback():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "change-me" not in text
    assert "POSTGRES_PASSWORD:?" in text


def test_every_service_that_uses_the_password_requires_it():
    services = _compose()["services"]
    for name in ("db", "web", "worker"):
        env = services[name].get("environment", {})
        password_entries = [value for value in env.values() if "POSTGRES_PASSWORD" in str(value)]
        if name == "db":
            assert env["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")
        else:
            assert password_entries, f"{name} should build its DATABASE_URL from POSTGRES_PASSWORD"
            assert all(":?" in str(value) for value in password_entries), name
