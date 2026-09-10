from functools import lru_cache

from pydantic import SecretStr, model_validator
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
    llm_api_key: SecretStr | None = SecretStr("ollama")
    llm_api_base_url: str | None = "http://127.0.0.1:11434/v1"
    llm_model: str | None = "llama3.2:3b"
    llm_timeout_seconds: float = 120
    research_run_limit_per_day: int = 20
    research_worker_poll_seconds: float = 2
    run_research_inline: bool = False
    max_request_body_bytes: int = 26_214_400

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
