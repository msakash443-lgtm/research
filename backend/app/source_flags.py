"""Reading a source's system-set `quality_flags` (pure; no DB)."""
from __future__ import annotations

PREPRINT_LABEL = "preprint, not peer reviewed"


def is_preprint(source) -> bool:
    """True when a service flagged the source a preprint (arXiv, Crossref, OpenAlex, PubMed, Semantic Scholar)."""
    return "preprint" in (getattr(source, "quality_flags", None) or [])
