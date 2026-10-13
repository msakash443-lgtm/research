"""Verify extraction evidence against the stored source full text (M3.4)."""

from __future__ import annotations

from typing import Any

from app.fulltext import FullText


def check_evidence_spans(
    fulltext: FullText,
    fields: dict[str, Any],
    evidence: dict[str, Any],
) -> list[str]:
    """Return field-specific errors for claims without a matching cited span.

    Any future human-verification path (M3.6) must call this against the
    source's stored PDF before setting ``verified_by_human``. Current extraction
    write paths call it before persistence and always store unverified rows.
    """
    problems = []
    for field, value in fields.items():
        if value is None:
            continue
        span = evidence.get(field)
        if not isinstance(span, dict):
            # Shape errors are reported by check_extraction; keep this guard so
            # callers cannot accidentally persist unchecked evidence.
            problems.append(f"{field}: no verifiable evidence span")
            continue
        matches = fulltext.find_quote(
            span["quote"], page=span.get("page"), section=span.get("section")
        )
        if not matches:
            anchor = []
            if span.get("page") is not None:
                anchor.append(f"page {span['page']}")
            if span.get("section") is not None:
                anchor.append(f"section {span['section']!r}")
            location = f" at cited {' and '.join(anchor)}" if anchor else ""
            problems.append(f"{field}: quote does not appear verbatim{location} in the stored source")
    return problems
