"""Register of outside services that receive data from this installation (X.4.9; GDPR Art. 13/30, DPDP notice).

Built from the live settings, so it lists only what is switched on and never goes stale. It names the host,
never the full URL or any credential: userinfo, path and query are dropped. What each service receives is
stated in plain words so the in-app notice can show it to every user.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app import survey_google_forms
from app.config import Settings
from app.connectors.access import CATALOGUE

NOTICE = (
    "This installation sends some of your project data to the outside services listed here, and only these. "
    "A service appears in the list only while it is switched on. Data kept on this server (your account, notes, "
    "projects, files and the audit log) is not listed. You can get a copy of your own data and have your account "
    "erased; ask the person who runs this installation how."
)


def _host(url: str | None) -> str | None:
    return urlsplit(url).hostname if url else None


def _entry(id_: str, label: str, purpose: str, data_sent: str, url: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"id": id_, "label": label, "purpose": purpose, "data_sent": data_sent, "host": _host(url), **extra}


def processor_register(settings: Settings) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if settings.llm_api_base_url:
        out.append(
            _entry(
                "llm", "AI model provider", "Pre-screening, extraction, drafting suggestions and research answers",
                "Questions, project context and source excerpts sent in prompts", settings.llm_api_base_url,
            )
        )
    # A local model on a private host stays inside the installation; one allowed onto a public host does not (M0.8.8).
    if settings.local_llm_api_base_url and settings.local_llm_allow_public_host:
        out.append(
            _entry(
                "local_llm", "Self-hosted AI model on a public address", "AI steps that must not use the provider above",
                "Questions, project context, source excerpts and participant data sent in prompts",
                settings.local_llm_api_base_url,
            )
        )
    if settings.embedding_model:
        out.append(
            _entry(
                "embeddings", "Embedding provider", "Grouping sources by theme",
                "Titles and abstracts of project sources", settings.embedding_api_base_url or settings.llm_api_base_url,
            )
        )
    if settings.arc_retrieval_enabled:
        out.append(
            _entry(
                "arc", "ARC retrieval service", "Web and scholar search, page crawl and PDF text extraction",
                "The research topic and search terms", settings.arc_retrieval_base_url,
            )
        )
    for name in settings.connectors_enabled:
        info = CATALOGUE.get(name)
        if info is None:
            continue
        sent = "Search queries and record identifiers"
        if settings.connector_contact_email:
            sent += f"; the contact address {settings.connector_contact_email} (sent so the service can reach the operator)"
        out.append(
            _entry(
                f"connector:{name}", info.label, "Searching the scholarly literature", sent,
                access=info.access.value,
            )
        )
    # Unpaywall (with OpenAlex/CORE) and arXiv hand back PDF links; the worker then downloads from whichever host serves them.
    if {"unpaywall", "arxiv"} & set(settings.connectors_enabled):
        out.append(
            _entry(
                "fulltext_hosts", "Publisher, repository and arXiv servers", "Downloading open-access full text",
                "Requests for the PDF of an open-access copy, made from this server; the host varies by paper",
            )
        )
    if settings.google_forms_access_token and settings.google_forms_access_token.get_secret_value().strip():
        out.append(
            _entry(
                "google_forms", "Google Forms", "Importing survey responses as a dataset",
                "The form id and the owner's access token; Google stores the form's questions and responses",
                survey_google_forms.BASE_URL,
            )
        )
    if settings.zotero_sync_enabled:
        out.append(
            _entry(
                "zotero", "Zotero", "Importing from and pushing verified sources to a Zotero library",
                "Reference metadata of verified sources", "https://api.zotero.org",
            )
        )
    if settings.object_storage_backend == "s3":
        out.append(
            _entry(
                "object_storage", "Object storage (S3-compatible)", "Storing PDFs, full text and note attachments",
                "Stored files", settings.object_storage_s3_endpoint_url,
            )
        )
    if settings.google_client_id and settings.google_client_secret:
        out.append(
            _entry(
                "google_sign_in", "Google sign-in", "Signing in with a Google account",
                "Sign-in request; Google returns your email and name", "https://accounts.google.com",
            )
        )
    return out
