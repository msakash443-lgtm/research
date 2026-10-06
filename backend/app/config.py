from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./research_ai.db"
    environment: str = "development"
    session_secret: SecretStr = SecretStr("development-only-change-me")
    public_origin: str = "http://localhost:8000"
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    cors_origins: list[str] = []
    auto_create_schema: bool = True
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str = "http://localhost:8000/api/auth/callback"
    llm_api_key: SecretStr | None = None
    llm_api_base_url: str | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = 60
    llm_schema_max_attempts: int = 3
    # AI pre-screen (M2.2): an answer below this confidence is recorded as "maybe", never include/exclude.
    prescreen_min_confidence: float = Field(default=0.6, ge=0, le=1)
    # Local/self-hosted OpenAI-compatible endpoint (e.g. vLLM, Ollama, LM Studio) for
    # participant/sensitive data that must never reach the third-party model above.
    # `api_key` is optional: most local servers don't require one.
    local_llm_api_key: SecretStr | None = None
    local_llm_api_base_url: str | None = None
    local_llm_model: str | None = None
    local_llm_timeout_seconds: float = 120
    # Safe default: participant data is refused (loudly) rather than silently sent to
    # LLM_API_BASE_URL unless a local model is configured. Set false only after an
    # explicit decision that the third-party model may see participant data (Q4).
    participant_data_requires_local_model: bool = True
    research_run_limit_per_day: int = 20
    research_worker_poll_seconds: float = 2
    research_run_lease_seconds: int = 900
    research_run_max_attempts: int = 3
    task_lease_seconds: int = 300
    task_retry_base_seconds: int = 5
    task_retry_max_seconds: int = 300
    run_research_inline: bool = False
    max_request_body_bytes: int = 1_048_576
    arc_retrieval_enabled: bool = False
    arc_retrieval_base_url: str | None = None
    arc_retrieval_token: SecretStr | None = None
    arc_retrieval_timeout_seconds: float = 120
    arc_max_web_results: int = 8
    arc_max_scholar_results: int = 5
    arc_max_crawl_urls: int = 5
    # Scholarly connectors (M1.3/M1.4). Off unless named here; names come from connectors.access.CATALOGUE.
    connectors_enabled: list[str] = []
    # Connectors whose access kind is `scraping` stay off even if named above, unless this is true.
    connectors_allow_scraping: bool = False
    # Contact address sent to scholarly APIs' "polite pool" (OpenAlex now, Crossref later). Optional; no placeholder.
    connector_contact_email: str | None = None
    # Optional Semantic Scholar API key (sent as x-api-key). Without one the shared, heavily throttled pool is used.
    semantic_scholar_api_key: SecretStr | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if bool(self.local_llm_api_base_url) != bool(self.local_llm_model):
            raise ValueError("LOCAL_LLM_API_BASE_URL and LOCAL_LLM_MODEL must be set together")
        if self.environment != "production":
            return self

        secret = self.session_secret.get_secret_value()
        placeholders = {"", "development-only-change-me", "replace-with-a-long-random-value"}
        if secret in placeholders or len(secret) < 32:
            raise ValueError("SESSION_SECRET must be a non-placeholder value of at least 32 characters in production")
        if self.database_url.startswith("sqlite"):
            raise ValueError("DATABASE_URL must use a managed PostgreSQL database in production")
        if self.auto_create_schema:
            raise ValueError("AUTO_CREATE_SCHEMA must be false in production; use Alembic migrations")
        if not self.public_origin.startswith("https://"):
            raise ValueError("PUBLIC_ORIGIN must use HTTPS in production")
        if not self.google_client_id or not self.google_client_secret:
            raise ValueError("Google OAuth credentials are required in production")
        if not self.google_redirect_uri.startswith(self.public_origin):
            raise ValueError("GOOGLE_REDIRECT_URI must start with PUBLIC_ORIGIN")
        from app.connectors.access import unknown_connectors

        bad = unknown_connectors(self.connectors_enabled)
        if bad:
            raise ValueError("CONNECTORS_ENABLED names an unknown connector: " + ", ".join(bad))
        if self.arc_retrieval_enabled:
            if not self.arc_retrieval_base_url or not self.arc_retrieval_base_url.startswith("https://"):
                raise ValueError("ARC_RETRIEVAL_BASE_URL must use HTTPS in production when ARC_RETRIEVAL_ENABLED is true")
            if not self.arc_retrieval_token or len(self.arc_retrieval_token.get_secret_value()) < 16:
                raise ValueError("ARC_RETRIEVAL_TOKEN must be set in production when ARC_RETRIEVAL_ENABLED is true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
