import ipaddress
import re
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_NAME_SUFFIXES = (".localhost", ".local", ".internal")
# A single-label name such as a Docker service (`ollama`). It must start with a letter, so numeric
# shorthands like `2130706433` or `0x7f000001`, which resolvers may turn into any IPv4 address, don't count.
_SINGLE_LABEL = re.compile(r"[a-z][a-z0-9-]{0,62}")


def local_llm_url_problem(url: str) -> str | None:
    """Why `url` doesn't name a local/private host, or None if it does (M0.8.8, Decisions log 2026-10-08).

    Judged from the URL text only, with no DNS lookup: loopback, private and link-local IP addresses;
    `localhost` and names under `.localhost`/`.local`/`.internal`; or a single-label name.
    """
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").rstrip(".")
    except ValueError:
        return "is not a valid URL"
    if parts.scheme not in {"http", "https"} or not host:
        return "must be an http(s) URL with a host"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if address.is_loopback or address.is_private or address.is_link_local:
            return None
        return f"points at {host}, which is not a loopback, private or link-local address"
    if host == "localhost" or host.endswith(_LOCAL_NAME_SUFFIXES) or _SINGLE_LABEL.fullmatch(host):
        return None
    return f"points at {host}, which is not a local or private host name"


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
    # LOCAL_LLM_API_BASE_URL must name a local/private host (`local_llm_url_problem`). Set true only on purpose,
    # for a self-hosted server reachable at a public address (M0.8.8).
    local_llm_allow_public_host: bool = False
    # Safe default: participant data is refused (loudly) rather than silently sent to
    # LLM_API_BASE_URL unless a local model is configured. Set false only after an
    # explicit decision that the third-party model may see participant data (Q4).
    participant_data_requires_local_model: bool = True
    research_run_limit_per_day: int = 20
    # Web/scholar-retrieval runs cost more and reach outside services, so they get a lower daily cap (M0.9.2).
    research_run_retrieval_limit_per_day: int = Field(default=5, ge=0)
    # Total model tokens a project may use before further calls are refused. 0 = no limit (M0.9.1).
    project_token_budget: int = Field(default=0, ge=0)
    research_worker_poll_seconds: float = 2
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
    # Seconds a connector may answer an identical request from memory (M1.3.3). 0 = off, so searches stay live.
    connector_cache_ttl_seconds: float = Field(default=0, ge=0)
    # Optional Semantic Scholar API key (sent as x-api-key). Without one the shared, heavily throttled pool is used.
    semantic_scholar_api_key: SecretStr | None = None
    # Optional NCBI E-utilities key for the PubMed connector (raises its rate limit from 3 to 10 requests a second).
    ncbi_api_key: SecretStr | None = None
    # CORE API key (M1.4.6). CORE has no anonymous access: enabling `core` without a key fails when it is used.
    core_api_key: SecretStr | None = None
    # Optional OpenCitations access token (sent as the `authorization` header).
    opencitations_access_token: SecretStr | None = None
    # Object storage for PDFs/full text (M0.10.2/M0.10.3). "local" or "s3"; any other value fails loudly.
    object_storage_backend: str = "local"
    object_storage_root: str = "./data/objects"
    object_storage_max_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    # S3-compatible backend (M0.10.3): any service that speaks the S3 REST API with SigV4 auth (AWS S3,
    # MinIO, Cloudflare R2, Backblaze B2's S3-compatible endpoint, …). Only read when
    # OBJECT_STORAGE_BACKEND=s3; credentials are never logged. `path_style` addresses objects as
    # `<endpoint>/<bucket>/<key>` (works for every S3-compatible service and for AWS outside new
    # public buckets); set it false for virtual-hosted-style (`<bucket>.<endpoint-host>/<key>`).
    object_storage_s3_endpoint_url: str | None = None
    object_storage_s3_bucket: str | None = None
    object_storage_s3_region: str = "us-east-1"
    object_storage_s3_access_key_id: str | None = None
    object_storage_s3_secret_access_key: SecretStr | None = None
    object_storage_s3_path_style: bool = True
    # Conditional PUT (`If-None-Match: *`) is the write-once mechanism; a backend that rejects the
    # header (400/405/501) falls back to check-then-put automatically. Set false to skip straight to
    # the fallback for a backend known not to support conditional writes.
    object_storage_s3_conditional_put: bool = True
    # Open-access full-text fetch (M2.7.1): Unpaywall licences under which a PDF may be stored. Anything
    # else (unknown, "implied-oa", "publisher-specific-oa") keeps the link and metadata only.
    fulltext_store_licences: list[str] = ["cc0", "pd", "public-domain", "cc-by", "cc-by-sa", "cc-by-nd", "cc-by-nc", "cc-by-nc-sa", "cc-by-nc-nd"]
    fulltext_fetch_timeout_seconds: float = Field(default=60, gt=0)
    # PDF text extraction (M2.7.3): refuse PDFs with more pages than this rather than parse them.
    fulltext_max_pages: int = Field(default=2000, gt=0)
    # Thematic clustering (M3.8.1): embeddings from an OpenAI-compatible /embeddings endpoint. Off while
    # EMBEDDING_MODEL is unset. The base URL and key default to LLM_API_BASE_URL / LLM_API_KEY.
    embedding_model: str | None = None
    embedding_api_base_url: str | None = None
    embedding_batch_size: int = Field(default=64, ge=1, le=2048)
    cluster_max_sources: int = Field(default=1000, ge=2)
    # Obsidian vault export (X.34.1): GET /api/projects/{id}/export/obsidian. Off by default; 404 while off.
    obsidian_export_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if bool(self.local_llm_api_base_url) != bool(self.local_llm_model):
            raise ValueError("LOCAL_LLM_API_BASE_URL and LOCAL_LLM_MODEL must be set together")
        if self.local_llm_api_base_url and not self.local_llm_allow_public_host:
            problem = local_llm_url_problem(self.local_llm_api_base_url)
            if problem:
                raise ValueError(
                    f"LOCAL_LLM_API_BASE_URL {problem}. Participant data may only go to a self-hosted model: use a "
                    "local/private address, or set LOCAL_LLM_ALLOW_PUBLIC_HOST=true if this public address is your own server"
                )
        if self.object_storage_backend == "s3":
            missing = [
                name
                for name, value in (
                    ("OBJECT_STORAGE_S3_ENDPOINT_URL", self.object_storage_s3_endpoint_url),
                    ("OBJECT_STORAGE_S3_BUCKET", self.object_storage_s3_bucket),
                    ("OBJECT_STORAGE_S3_ACCESS_KEY_ID", self.object_storage_s3_access_key_id),
                )
                if not value
            ]
            if not self.object_storage_s3_secret_access_key or not self.object_storage_s3_secret_access_key.get_secret_value():
                missing.append("OBJECT_STORAGE_S3_SECRET_ACCESS_KEY")
            if missing:
                raise ValueError("OBJECT_STORAGE_BACKEND=s3 requires " + ", ".join(missing))
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
        if "core" in self.connectors_enabled and not (self.core_api_key and self.core_api_key.get_secret_value().strip()):
            raise ValueError("CORE_API_KEY must be set in production when 'core' is in CONNECTORS_ENABLED")
        if self.arc_retrieval_enabled:
            if not self.arc_retrieval_base_url or not self.arc_retrieval_base_url.startswith("https://"):
                raise ValueError("ARC_RETRIEVAL_BASE_URL must use HTTPS in production when ARC_RETRIEVAL_ENABLED is true")
            if not self.arc_retrieval_token or len(self.arc_retrieval_token.get_secret_value()) < 16:
                raise ValueError("ARC_RETRIEVAL_TOKEN must be set in production when ARC_RETRIEVAL_ENABLED is true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
