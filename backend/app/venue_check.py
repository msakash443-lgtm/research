"""Compare a source's venue/ISSN with the project's owner-supplied predatory list (X.201). Pure: no DB, no network.

A match is evidence for a person to judge, never a verdict: it adds a warning flag, it never blocks a citation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

PREDATORY_FLAG = "predatory_venue_match"
_ISSN = re.compile(r"^\d{4}-?\d{3}[\dXx]$")


@dataclass(frozen=True)
class ListedVenue:
    """One journal on a list the owner supplied: its name, ISSNs and the list's provenance (`list_name`)."""
    name: str
    issns: tuple[str, ...]
    list_name: str


def normalise_issn(value: object) -> str | None:
    """`1234-567X` form, or None when it is not an ISSN."""
    text = str(value or "").strip().replace("\u2010", "-").replace("\u2013", "-")
    if not _ISSN.match(text):
        return None
    text = text.replace("-", "").upper()
    return f"{text[:4]}-{text[4:]}"


def _name_key(value: str | None) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", (value or "").casefold()).split())


def source_issns(source_ids: object) -> set[str]:
    """ISSNs a service recorded for the source (`source_ids["issn"]`, one string or a list)."""
    raw = source_ids.get("issn") if isinstance(source_ids, dict) else None
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    return {issn for value in values if (issn := normalise_issn(value))}


def match_venue(venue: str | None, issns: Iterable[str], listed: Iterable[ListedVenue]) -> dict | None:
    """The first listed journal the source's ISSN or venue name matches, with the list name and the reason.
    ISSN is checked before name, and a name must match whole (case/punctuation-insensitive), never by substring."""
    wanted = set(issns)
    key = _name_key(venue)
    by_name = None
    for entry in listed:
        entry_issns = {issn for value in entry.issns if (issn := normalise_issn(value))}
        if wanted & entry_issns:
            return {"list": entry.list_name, "journal": entry.name, "reason": "issn", "issn": sorted(wanted & entry_issns)[0]}
        if by_name is None and key and key == _name_key(entry.name):
            by_name = {"list": entry.list_name, "journal": entry.name, "reason": "venue_name"}
    return by_name
