"""Which connectors exist, how each one accesses its service, and which are switched on.

The access kind is the per-source ToS/licence flag from spec section 6 ("use official APIs, not
scraping; respect ToS"):

* `official_api`: a documented public API used within its terms.
* `licensed`: needs a paid/institutional licence and key; never usable without one.
* `scraping`: reads pages that have no sanctioned API. Off by default and needs a second switch.

Nothing is enabled by default (plan rule 18). A connector runs only if its name is in
`CONNECTORS_ENABLED`; a `scraping` connector additionally needs `CONNECTORS_ALLOW_SCRAPING=true`.
The flag lives on the catalogue entry, not on the connector class, so it can be shown and audited
before any code for that connector exists. `implemented` stays False until its connector (M1.4)
lands, and an enabled-but-unimplemented connector is reported as such, never as usable.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class AccessKind(str, enum.Enum):
    official_api = "official_api"
    licensed = "licensed"
    scraping = "scraping"


@dataclass(frozen=True)
class ConnectorInfo:
    name: str
    label: str
    access: AccessKind
    implemented: bool = False  # flipped by the task that builds the connector
    note: str = ""


_ENTRIES = (
    ConnectorInfo("openalex", "OpenAlex", AccessKind.official_api, implemented=True),
    ConnectorInfo("crossref", "Crossref", AccessKind.official_api, implemented=True),
    ConnectorInfo("semantic_scholar", "Semantic Scholar", AccessKind.official_api, implemented=True),
    ConnectorInfo("arxiv", "arXiv", AccessKind.official_api, implemented=True),
    ConnectorInfo("unpaywall", "Unpaywall", AccessKind.official_api, implemented=True),
    ConnectorInfo("pubmed", "PubMed / Europe PMC", AccessKind.official_api),
    ConnectorInfo("core", "CORE", AccessKind.official_api),
    ConnectorInfo("opencitations", "OpenCitations", AccessKind.official_api),
    ConnectorInfo("scopus", "Scopus", AccessKind.licensed, note="Needs an institutional licence and key."),
    ConnectorInfo("web_of_science", "Web of Science", AccessKind.licensed, note="Needs an institutional licence and key."),
    ConnectorInfo("ieee_xplore", "IEEE Xplore", AccessKind.licensed, note="Needs an API key under licence."),
    ConnectorInfo(
        "google_scholar", "Google Scholar", AccessKind.scraping, note="No official API; page scraping may breach its terms."
    ),
)

CATALOGUE: dict[str, ConnectorInfo] = {entry.name: entry for entry in _ENTRIES}


def unknown_connectors(names: list[str]) -> list[str]:
    return sorted({name for name in names if name not in CATALOGUE})


def is_enabled(name: str, enabled: list[str], allow_scraping: bool) -> bool:
    info = CATALOGUE.get(name)
    if info is None or name not in enabled:
        return False
    return allow_scraping or info.access is not AccessKind.scraping
