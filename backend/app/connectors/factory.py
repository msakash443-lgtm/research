"""Build a connector by catalogue name, with its settings (contact email, API key)."""

from __future__ import annotations

from app.config import get_settings
from app.connectors.base import Connector, ConnectorError
from app.connectors.arxiv import ArxivConnector
from app.connectors.core import CoreConnector
from app.connectors.crossref import CrossrefConnector
from app.connectors.ieee_xplore import IeeeXploreConnector
from app.connectors.openalex import OpenAlexConnector
from app.connectors.opencitations import OpenCitationsConnector
from app.connectors.pubmed import PubMedConnector
from app.connectors.scopus import ScopusConnector
from app.connectors.semantic_scholar import SemanticScholarConnector
from app.connectors.unpaywall import UnpaywallConnector
from app.connectors.web_of_science import WebOfScienceConnector

# Connectors that can run a keyword search. Unpaywall and OpenCitations look works up by id only.
SEARCHABLE = (
    "openalex", "crossref", "semantic_scholar", "arxiv", "pubmed", "core",
    "scopus", "web_of_science", "ieee_xplore",
)


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
    if name == "pubmed":
        return PubMedConnector(contact_email=settings.connector_contact_email, api_key=settings.ncbi_api_key)
    if name == "core":
        return CoreConnector(api_key=settings.core_api_key)
    if name == "scopus":
        return ScopusConnector(api_key=settings.scopus_api_key, inst_token=settings.scopus_inst_token)
    if name == "web_of_science":
        return WebOfScienceConnector(api_key=settings.web_of_science_api_key)
    if name == "ieee_xplore":
        return IeeeXploreConnector(api_key=settings.ieee_xplore_api_key)
    if name == "opencitations":
        return OpenCitationsConnector(access_token=settings.opencitations_access_token)
    raise ConnectorError(f"No connector named '{name}'")


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
