"""Institutional similarity providers (X.31.15): the interface and a registry, with no connector yet.

An institutional check (iThenticate, Turnitin) would send the section's text to a third party under the institution's
licence. Whether the owner has such a licence is open (Q17), so no connector is registered: `CONNECTORS` is empty and
every institutional request is "not configured". A connector is a factory added to `CONNECTORS` once a licence is
confirmed; it is used only when `similarity_institutional_provider` names it (the text must leave the server only
after the researcher confirms the "what will happen" hint in the design doc).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from app.config import Settings

NOT_CONNECTED_HINT = "Not connected: ask your institution"
PROJECT_SOURCES_ID = "project_sources"
INSTITUTIONAL_ID = "institutional"


@dataclass(frozen=True)
class ProviderFinding:
    """One flagged span in the submitted text, as an external service reports it."""

    start: int
    end: int
    score: float
    matched: str | None


class SimilarityProvider(Protocol):
    name: str

    def submit(self, text: str, metadata: dict[str, Any]) -> str:
        """Send the text to the service and return its reference for the submission."""

    def result(self, provider_ref: str) -> list[ProviderFinding]:
        """The findings for a finished submission."""


# provider name -> factory taking the settings. Empty until a licence is confirmed (Q17).
CONNECTORS: dict[str, Callable[[Settings], SimilarityProvider]] = {}


def institutional_provider(settings: Settings) -> SimilarityProvider | None:
    """The connector the settings name, or None when none is named or none is registered under that name."""
    name = settings.similarity_institutional_provider.strip()
    factory = CONNECTORS.get(name) if name else None
    return factory(settings) if factory else None


def provider_statuses(settings: Settings) -> list[dict[str, Any]]:
    available = institutional_provider(settings) is not None
    return [
        {"id": PROJECT_SOURCES_ID, "label": "Project sources", "available": True, "hint": None},
        {"id": INSTITUTIONAL_ID, "label": "Institutional check", "available": available, "hint": None if available else NOT_CONNECTED_HINT},
    ]
