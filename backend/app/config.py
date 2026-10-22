import ipaddress
import os
import re
from functools import lru_cache
from typing import Any, Literal, get_args
from urllib.parse import parse_qs, urlsplit

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


def _database_requests_tls(url: str) -> bool:
    """True when the URL's `sslmode` asks for encryption that is verified or at least required (X.4.1)."""
    modes = parse_qs(urlsplit(url).query).get("sslmode", [])
    return bool(modes) and modes[-1] in {"require", "verify-ca", "verify-full"}


def _file_secret_fields(settings_cls: type[BaseSettings]) -> list[str]:
    """Names of the settings that may be supplied as `<NAME>_FILE`: `database_url` and every secret field."""
    names = ["database_url"]
    for name, field in settings_cls.model_fields.items():
        annotation = field.annotation
        if SecretStr is annotation or SecretStr in get_args(annotation):
            names.append(name)
    return names


class Settings(BaseSettings):
    database_url: str = "sqlite:///./research_ai.db"
    # Required, and exactly one of these (X.105): the production checks run only for "production", so any
    # other value (a typo, a different case, "prod") refuses to start instead of silently running without
    # them. No default: an unset value would otherwise turn on the development and X-User-Id logins.
    environment: Literal["development", "production"]
    session_secret: SecretStr = SecretStr("development-only-change-me")
    # Browser session lifetime (X.4.3). Starlette's own default is 14 days.
    session_max_age_seconds: int = Field(default=14 * 24 * 60 * 60, gt=0)
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
    # AI auto-exclude (M2.5.1, spec 5.3 human gate): an AI title/abstract exclude at or above this
    # confidence can count as the final decision without a person looking at that record individually,
    # but only while it stays inside the running human sampling audit below. Off per project
    # (`projects.ai_autoexclude_enabled`) until an owner turns it on.
    screening_ai_autoexclude_confidence_threshold: float = Field(default=0.9, ge=0, le=1)
    # Fraction of eligible AI excludes a person must review (confirm/overturn) before they count;
    # every 1/rate-th eligible exclude is sampled. Must be > 0: an auto-exclude policy with no audit
    # at all is exactly what spec 5.3's human gate forbids.
    screening_ai_autoexclude_sample_rate: float = Field(default=0.10, gt=0, le=1)
    # Quality guard (spec 5.3): once an AI suggestion and a person's own eventual decision exist for
    # the same source+stage, their agreement (Cohen's kappa) is tracked; below this, with at least
    # `..._min_n` comparable pairs, AI pre-screen auto-disables for the project until an owner re-enables it.
    screening_ai_agreement_min_kappa: float = Field(default=0.4, ge=-1, le=1)
    screening_ai_agreement_min_n: int = Field(default=20, ge=1)
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
    # Production only: LOCAL_LLM_API_BASE_URL is https unless this is true (X.4.1). Use it for a local model
    # on a private network that serves plain http.
    local_llm_allow_http: bool = False
    # Production only: DATABASE_URL must request TLS (`sslmode=require`, `verify-ca` or `verify-full`).
    # Set true only for a local socket or sidecar database that never leaves the host (X.4.1).
    database_tls_opt_out: bool = False
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
    # X.4.7.4: run-snapshot retention. None/unset = kept indefinitely (no purge runs). When set, the worker
    # clears `ResearchRun.input_snapshot` for finished runs (completed/failed/needs_sources/needs_configuration)
    # whose `completed_at` is older than this many days. The answer text, status and audit trail are untouched.
    research_run_snapshot_retention_days: int | None = Field(default=None, ge=1)
    # X.4.10: installation-wide privacy profile (app/privacy_profiles/<name>.toml) and the region the database
    # and object store are hosted in. The profile supplies defaults; an explicit setting above wins.
    privacy_profile: str = "default"
    data_residency_region: str | None = None
    task_lease_seconds: int = 300
    # X.115: longest a task type without its own max_run_seconds may run while its lease is renewed.
    task_max_run_seconds: int = Field(default=3600, gt=0)
    task_retry_base_seconds: int = 5
    task_retry_max_seconds: int = 300
    run_research_inline: bool = False
    max_request_body_bytes: int = 1_048_576
    # X.143: per-process, per-minute caps on sign-in and upload requests (429 + Retry-After). Protection,
    # so on by default; the reverse proxy should still limit the edge (docs/DEPLOYMENT.md).
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = Field(default=20, gt=0)
    rate_limit_upload_per_minute: int = Field(default=30, gt=0)
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
    # Share the circuit breaker (M1.3.2) across processes via a DB row (M1.3.8), instead of each
    # freshly built `ConnectorHttpClient` (connectors/factory.py::build_connector is called per
    # task) starting a fresh in-memory breaker that never actually trips. Off by default.
    connector_breaker_shared: bool = False
    # Optional Semantic Scholar API key (sent as x-api-key). Without one the shared, heavily throttled pool is used.
    semantic_scholar_api_key: SecretStr | None = None
    # Optional NCBI E-utilities key for the PubMed connector (raises its rate limit from 3 to 10 requests a second).
    ncbi_api_key: SecretStr | None = None
    # CORE API key (M1.4.6). CORE has no anonymous access: enabling `core` without a key fails when it is used.
    core_api_key: SecretStr | None = None
    # Licensed scholarly indexes (M1.4.7). Credentials are used only when the named connector is enabled.
    scopus_api_key: SecretStr | None = None
    scopus_inst_token: SecretStr | None = None
    web_of_science_api_key: SecretStr | None = None
    ieee_xplore_api_key: SecretStr | None = None
    # Optional OpenCitations access token (sent as the `authorization` header).
    opencitations_access_token: SecretStr | None = None
    # Object storage for PDFs/full text (M0.10.2/M0.10.3). "local" or "s3"; any other value fails loudly.
    # Key for keyed pseudonymisation of dataset columns (M5.6). Never logged or stored with the data; without it the
    # pseudonymise step is refused rather than falling back to an unkeyed hash.
    pseudonym_key: SecretStr | None = None

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
    # Institutional similarity check (X.31.15): the name of a registered connector in `similarity_providers.CONNECTORS`.
    # Empty = none. No connector exists until a licence is confirmed (Q17), so any value is "not configured" for now.
    similarity_institutional_provider: str = ""
    # Zotero library sync (M1.11.2): pull items from, and push verified sources into, a Zotero user
    # library over the official API. Off by default; the credentials are used only while enabled.
    # `zotero_collection_key` is the collection pushes create items in; pulls read the whole library
    # (or that collection when set).
    zotero_sync_enabled: bool = False
    zotero_api_key: SecretStr | None = None
    zotero_user_id: str | None = None
    zotero_collection_key: str | None = None
    # Google Forms connector (M5.2): a short-lived OAuth access token the owner obtains for their own Google
    # account (scope: Forms responses read). Env-only; never stored in the database or echoed in errors.
    # Unset = the import route answers 503.
    google_forms_access_token: SecretStr | None = None
    # Analysis sandbox (M5.3.3): a Docker image pinned by digest (name@sha256:...). Unset = runs answer 503.
    analysis_sandbox_image: str | None = None
    analysis_sandbox_timeout_seconds: int = 120

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def read_secret_files(cls, data: Any) -> Any:
        """Fill secret fields from `<NAME>_FILE` (Docker/Kubernetes secrets, X.4.5).

        Only `DATABASE_URL` and the `SecretStr` fields are eligible. Setting both `NAME` and `NAME_FILE`
        is refused, and an unreadable file fails start-up; neither error quotes a secret value.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for name in _file_secret_fields(cls):
            env_name = f"{name.upper()}_FILE"
            path = os.environ.get(env_name)
            if not path:
                continue
            if data.get(name) not in (None, ""):
                raise ValueError(f"set {name.upper()} or {env_name}, not both")
            try:
                with open(path, encoding="utf-8") as handle:
                    data[name] = handle.read().rstrip("\r\n")
            except OSError as exc:
                raise ValueError(f"{env_name} points at a file that cannot be read ({exc.strerror})") from None
        return data

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
        if self.zotero_sync_enabled:
            missing = []
            if not self.zotero_api_key or not self.zotero_api_key.get_secret_value().strip():
                missing.append("ZOTERO_API_KEY")
            if not (self.zotero_user_id or "").strip():
                missing.append("ZOTERO_USER_ID")
            if missing:
                raise ValueError("ZOTERO_SYNC_ENABLED=true requires " + ", ".join(missing))
        if self.pseudonym_key is not None:
            # Checked in every environment: a copied example value would key pseudonyms with a public string (M5.6).
            key = self.pseudonym_key.get_secret_value().strip()
            if not key or key.startswith("replace-with-"):
                raise ValueError("PSEUDONYM_KEY must be a real secret, or left unset; the example placeholder is not a key")
            if len(key) < 16:
                raise ValueError("PSEUDONYM_KEY must be at least 16 characters; a short key can be guessed and reverses pseudonyms")
        from app.privacy_profile import PrivacyProfileError, load_privacy_profile, residency_problem

        try:
            privacy = load_privacy_profile(self.privacy_profile)
        except PrivacyProfileError as exc:
            raise ValueError(str(exc)) from None
        if self.environment != "production":
            return self
        problem = residency_problem(privacy, self.data_residency_region)
        if problem:
            raise ValueError(problem)

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
        if self.llm_api_base_url and urlsplit(self.llm_api_base_url).scheme != "https":
            raise ValueError("LLM_API_BASE_URL must use HTTPS in production (research content is sent there)")
        if self.local_llm_api_base_url and urlsplit(self.local_llm_api_base_url).scheme != "https" and not self.local_llm_allow_http:
            raise ValueError("LOCAL_LLM_API_BASE_URL must use HTTPS in production; set LOCAL_LLM_ALLOW_HTTP=true for a private-network model that serves plain http")
        if not self.database_tls_opt_out and not _database_requests_tls(self.database_url):
            raise ValueError("DATABASE_URL must request TLS in production (add sslmode=require, verify-ca or verify-full), or set DATABASE_TLS_OPT_OUT=true for a local socket or sidecar")
        from app.connectors.access import unknown_connectors

        bad = unknown_connectors(self.connectors_enabled)
        if bad:
            raise ValueError("CONNECTORS_ENABLED names an unknown connector: " + ", ".join(bad))
        if "core" in self.connectors_enabled and not (self.core_api_key and self.core_api_key.get_secret_value().strip()):
            raise ValueError("CORE_API_KEY must be set in production when 'core' is in CONNECTORS_ENABLED")
        for connector, field_name, setting_name in (
            ("scopus", self.scopus_api_key, "SCOPUS_API_KEY"),
            ("web_of_science", self.web_of_science_api_key, "WEB_OF_SCIENCE_API_KEY"),
            ("ieee_xplore", self.ieee_xplore_api_key, "IEEE_XPLORE_API_KEY"),
        ):
            if connector in self.connectors_enabled and not (field_name and field_name.get_secret_value().strip()):
                raise ValueError(f"{setting_name} must be set in production when {connector!r} is in CONNECTORS_ENABLED")
        if self.arc_retrieval_enabled:
            if not self.arc_retrieval_base_url or not self.arc_retrieval_base_url.startswith("https://"):
                raise ValueError("ARC_RETRIEVAL_BASE_URL must use HTTPS in production when ARC_RETRIEVAL_ENABLED is true")
            if not self.arc_retrieval_token or len(self.arc_retrieval_token.get_secret_value()) < 16:
                raise ValueError("ARC_RETRIEVAL_TOKEN must be set in production when ARC_RETRIEVAL_ENABLED is true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
