import pytest

from app.config import Settings

# X.105: every production check runs only when ENVIRONMENT is exactly "production", so an unknown value
# (a typo, a different case, "prod") must refuse to start rather than run with none of those checks.

PRODUCTION = dict(
    environment="production",
    session_secret="x" * 40,
    database_url="postgresql+psycopg://u:p@db.internal:5432/research?sslmode=require",
    auto_create_schema=False,
    public_origin="https://lab.example.org",
    google_client_id="id",
    google_client_secret="secret",
    google_redirect_uri="https://lab.example.org/api/auth/callback",
    _env_file=None,
)


@pytest.mark.parametrize("value", ["Production", "PRODUCTION", "prod", "staging", "test", "Development", " production", ""])
def test_an_unknown_environment_value_refuses_to_start_instead_of_skipping_the_production_checks(value):
    with pytest.raises(ValueError, match="Input should be 'development' or 'production'"):
        Settings(_env_file=None, environment=value)


def test_a_typo_cannot_bypass_the_production_secret_check():
    # With a placeholder secret, "production" refuses; a mistyped "Production" must refuse too, not start.
    for value in ("production", "Production"):
        with pytest.raises(ValueError):
            Settings(**{**PRODUCTION, "environment": value, "session_secret": "development-only-change-me"})


def test_an_unset_environment_refuses_to_start_instead_of_defaulting_to_development(monkeypatch):
    # Defaulting to "development" would turn on the development login and the X-User-Id header login.
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    with pytest.raises(ValueError, match=r"environment\s+Field required"):
        Settings(_env_file=None)


def test_the_two_known_environments_are_accepted():
    assert Settings(_env_file=None, environment="development").environment == "development"
    assert Settings(**PRODUCTION).environment == "production"


def test_runtime_refuses_a_non_postgresql_database_in_every_environment():
    for environment in ("development", "production"):
        with pytest.raises(ValueError, match="DATABASE_URL must use PostgreSQL"):
            Settings(**{**PRODUCTION, "environment": environment, "database_url": "mysql+pymysql://u:p@db/research"})


def test_a_wildcard_cors_origin_is_refused_in_every_environment():
    for environment in ("development", "production"):
        with pytest.raises(ValueError, match="CORS_ORIGINS must list explicit origins"):
            Settings(**{**PRODUCTION, "environment": environment, "cors_origins": ["*"]})


def test_a_plain_http_cors_origin_is_refused_in_production_only():
    with pytest.raises(ValueError, match="CORS_ORIGINS must use HTTPS in production"):
        Settings(**{**PRODUCTION, "cors_origins": ["http://ui.example"]})
    assert Settings(**{**PRODUCTION, "environment": "development", "cors_origins": ["http://localhost:5173"]}).cors_origins
    assert Settings(**{**PRODUCTION, "cors_origins": ["https://ui.example"]}).cors_origins == ["https://ui.example"]
