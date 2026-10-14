"""Conservative, read-only flags for directly opposing extracted findings."""

from __future__ import annotations

import re


_DIRECTION = re.compile(r"^(.+?)\s+(increases|decreases)\s+(.+?)\.?$", re.IGNORECASE)


def detect(matrix: dict) -> list[dict]:
    """Flag only identical subject/outcome pairs with opposite explicit directions.

    These are review prompts, not judgments that either study is correct.
    """
    findings = []
    for paper in matrix["papers"]:
        cell = paper["cells"].get("key_findings", {})
        if not paper["verified_by_human"] or cell.get("status") != "checked" or not cell.get("quote"):
            continue
        values = cell.get("value") or []
        if not isinstance(values, list):
            continue
        for finding in values:
            if not isinstance(finding, dict) or not isinstance(finding.get("statement"), str):
                continue
            statement = finding["statement"].strip()
            match = _DIRECTION.fullmatch(statement)
            if match is None:
                continue
            key = (match[1].strip().casefold(), match[3].strip().casefold())
            citation = {
                "source_id": paper["source_id"], "title": paper["title"],
                "extraction_id": paper["extraction_id"], "statement": statement,
                "quote": cell["quote"], "page": cell.get("page"),
                "quote_scope": "key_findings_cell", "section": cell.get("section"),
                "source_text_url": cell.get("source_text_url"),
            }
            findings.append((key, match[2].casefold(), citation))
    flags = []
    for index, (key, direction, citation) in enumerate(findings):
        for other_key, other_direction, other_citation in findings[index + 1:]:
            if (key == other_key and direction != other_direction
                    and citation["source_id"] != other_citation["source_id"]):
                flags.append({"subject": key[0], "outcome": key[1],
                              "citations": [citation, other_citation]})
    return flags
