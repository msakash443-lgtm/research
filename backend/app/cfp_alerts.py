"""Researcher-supplied conference/CFP deadline alerts (M7.6.3, spec §5.13.5).

Pure: no database, network, discovery, or invented dates. Callers supply each opportunity's
name, ISO date, and authoritative source URL.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable


def classify_opportunities(opportunities: Iterable[dict[str, str]], *, today: date | None = None) -> list[dict[str, Any]]:
    """Return validated opportunities with deterministic ``upcoming``/``expired`` status.

    Required fields are non-blank ``name``, ISO ``deadline`` (YYYY-MM-DD), and ``source_url``.
    The source URL is copied unchanged; no URL is fetched or interpreted.
    """
    current = today or date.today()
    rows: list[dict[str, Any]] = []
    for item in opportunities:
        name = item.get("name", "").strip()
        deadline_text = item.get("deadline", "").strip()
        source_url = item.get("source_url", "").strip()
        if not name or not deadline_text or not source_url:
            raise ValueError("Each CFP opportunity needs a name, deadline, and source_url.")
        try:
            deadline = date.fromisoformat(deadline_text)
        except ValueError as exc:
            raise ValueError(f"Invalid CFP deadline {deadline_text!r}; expected YYYY-MM-DD.") from exc
        rows.append({
            "name": name,
            "deadline": deadline.isoformat(),
            "source_url": source_url,
            "status": "upcoming" if deadline >= current else "expired",
        })
    return sorted(rows, key=lambda row: (row["deadline"], row["name"], row["source_url"]))
