"""Build a connector by catalogue name, with its settings (contact email, API key)."""

from __future__ import annotations

from app.config import get_settings
from app.connectors.base import Connector, ConnectorError
from app.connectors.arxiv import ArxivConnector
from app.connectors.crossref import CrossrefConnector
from app.connectors.openalex import OpenAlexConnector
from app.connectors.semantic_scholar import SemanticScholarConnector
from app.connectors.unpaywall import UnpaywallConnector

# Connectors that can run a keyword search. Unpaywall resolves DOIs only, so it is not listed.
SEARCHABLE = ("openalex", "crossref", "semantic_scholar", "arxiv")


# Services the citation verifier asks, in this order.
LOOKUP = ("crossref", "semantic_scholar", "openalex")


def enabled_lookup_names() -> list[str]:
    from app.connectors.access import is_enabled

    settings = get_settings()
    return [n for n in LOOKUP if is_enabled(n, settings.connectors_enabled, settings.connectors_allow_scraping)]


def build_connector(name: str) -> Connector:
    settings = get_settings()
    if name == "openalex":
        return OpenAlexConnector(contact_email=settings.connector_contact_email)
    if name == "crossref":
        return CrossrefConnector(contact_email=settings.connector_contact_email)
    if name == "semantic_scholar":
        return SemanticScholarConnector(api_key=settings.semantic_scholar_api_key)
    if name == "arxiv":
        return ArxivConnector()
    raise ConnectorError(f"No searchable connector named '{name}'")


def unpaywall_unavailable_reason() -> str | None:
    """Why Unpaywall can't be used on this server (M2.7.1), or None when it can."""
    from app.connectors.access import is_enabled

    settings = get_settings()
    if not is_enabled("unpaywall", settings.connectors_enabled, settings.connectors_allow_scraping):
        return "The Unpaywall connector is not enabled on this server"
    if not settings.connector_contact_email:
        return "Unpaywall needs CONNECTOR_CONTACT_EMAIL to be set on this server"
    return None


def build_unpaywall() -> UnpaywallConnector:
    return UnpaywallConnector(contact_email=get_settings().connector_contact_email)
