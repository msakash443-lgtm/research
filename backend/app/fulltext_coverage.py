"""How much of a paper the project actually has (X.76, spec §6): the full text, only the abstract, or neither.

Pure: it reads a source's own fields and writes nothing. `full_text` means a PDF is stored (`fulltext_path`, set only by
the system); `abstract_only` means no PDF but a non-empty abstract; `not_accessible` means neither, so a reviewer
would be deciding from the title alone. `open_access_url` is the source's open-access location when it is a plain
http(s) link (never the stored PDF, never a script URL), so a person can open the open-access copy themselves.
`reason` says why a PDF is missing when the open-access lookup recorded one (`Source.fulltext_access`, M2.7.1).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

FULL_TEXT = "full_text"
ABSTRACT_ONLY = "abstract_only"
NOT_ACCESSIBLE = "not_accessible"

_WHY = {
    "link_only": "an open-access copy exists, but its licence does not allow storing it",
    "no_pdf": "an open-access page exists, but it has no PDF link",
    "no_oa": "no open-access copy was found",
    "lookup_failed": "the open-access lookup failed for at least one service",
}


def _http_url(value: str | None) -> str | None:
    text = (value or "").strip()
    parts = urlsplit(text)
    return text if parts.scheme in ("http", "https") and parts.netloc else None


def coverage(source: Any) -> dict[str, Any]:
    if source.fulltext_path:
        level = FULL_TEXT
    elif (source.abstract or "").strip():
        level = ABSTRACT_ONLY
    else:
        level = NOT_ACCESSIBLE
    status = (source.fulltext_access or {}).get("status")
    return {
        "level": level,
        "open_access_url": _http_url(source.oa_url),
        "reason": None if level == FULL_TEXT else _WHY.get(status),
    }
