import pytest

from app.config import Settings

# X.4.1: in production, data in transit must be encrypted. LLM endpoints must be https, the local
# participant-data model may use http only with an explicit opt-in, and Postgres must request TLS.

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


def _production(**overrides) -> Settings:
    return Settings(**{**PRODUCTION, **overrides})


def test_production_requires_https_llm_endpoint():
    with pytest.raises(ValueError, match="LLM_API_BASE_URL must use HTTPS"):
        _production(llm_api_base_url="http://llm.example.org/v1")
    assert _production(llm_api_base_url="https://llm.example.org/v1").llm_api_base_url == "https://llm.example.org/v1"
    # Unset is not checked here: the LLM simply isn't configured.
    assert _production(llm_api_base_url=None).llm_api_base_url is None


def test_production_refuses_http_local_model_without_opt_in():
    local = dict(local_llm_api_base_url="http://ollama:11434/v1", local_llm_model="m")
    with pytest.raises(ValueError, match="LOCAL_LLM_API_BASE_URL must use HTTPS"):
        _production(**local)
    accepted = _production(**local, local_llm_allow_http=True)
    assert accepted.local_llm_allow_http is True
    assert _production(local_llm_api_base_url="https://ollama:11434/v1", local_llm_model="m")


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://u:p@db.internal:5432/research",
    "postgresql+psycopg://u:p@db.internal:5432/research?sslmode=prefer",
    "postgresql+psycopg://u:p@db.internal:5432/research?sslmode=disable",
    "postgresql+psycopg://u:p@db.internal:5432/research?sslmode=allow",
])
def test_production_database_must_request_tls(url):
    with pytest.raises(ValueError, match="DATABASE_URL must request TLS"):
        _production(database_url=url)


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_production_accepts_strong_sslmode(mode):
    url = f"postgresql+psycopg://u:p@db.internal:5432/research?sslmode={mode}"
    assert _production(database_url=url).database_url == url


def test_production_database_tls_opt_out_is_explicit_only():
    plain = "postgresql+psycopg://u:p@/research?host=/var/run/postgresql"
    with pytest.raises(ValueError, match="DATABASE_URL must request TLS"):
        _production(database_url=plain)
    assert _production(database_url=plain, database_tls_opt_out=True).database_tls_opt_out is True


def test_development_does_not_enforce_transport_rules():
    settings = Settings(
        _env_file=None,
        environment="development",
        llm_api_base_url="http://llm.test/v1",
        local_llm_api_base_url="http://ollama:11434/v1",
        local_llm_model="m",
        database_url="postgresql+psycopg://u:p@localhost/research",
    )
    assert settings.llm_api_base_url == "http://llm.test/v1"
