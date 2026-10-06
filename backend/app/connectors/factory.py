"""Build a connector by catalogue name, with its settings (contact email, API key)."""

from __future__ import annotations

from app.config import get_settings
from app.connectors.base import Connector, ConnectorError
from app.connectors.crossref import CrossrefConnector
from app.connectors.openalex import OpenAlexConnector
from app.connectors.semantic_scholar import SemanticScholarConnector

# Connectors that can run a keyword search. Unpaywall resolves DOIs only, so it is not listed.
SEARCHABLE = ("openalex", "crossref", "semantic_scholar")


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
    raise ConnectorError(f"No searchable connector named '{name}'")
