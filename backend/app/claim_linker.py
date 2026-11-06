"""Claim–evidence checks for one manuscript section (M6.2). Pure: plain facts in, a report out, no database or network.

A section's ProseMirror text cites sources with `cite` nodes. The linker compares those citations with the section's
claims (M6.1 binds claims to sections) and with the project's sources:

- `unknown_source`: a citation names a source that is not an active source of this project.
- `unverified_citation`: a citation names a source whose metadata no person or check has verified. It is not in the
  reference list. The export already leaves such citations out (plan rule 24); this check reports them before export.
- `orphan_claim`: a claim in the section has no evidence linked.
- `unverified_evidence`: a claim is linked to an unverified source.
- `evidence_not_cited`: a claim is linked to a verified source the text never cites, so the reference list built from
  the text would miss it.

Verified means `metadata_verified`, the same rule `claims.verify` and the manuscript export use. The linker only
reports; it never blocks an export (that hard block is M6.4) and never changes a claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class SourceFacts:
    id: str
    verified: bool
    title: str


@dataclass(frozen=True)
class ClaimFacts:
    claim_id: str
    verified: bool
    evidence_source_ids: tuple[str, ...]


@dataclass(frozen=True)
class Finding:
    kind: str
    subject: str
    message: str


@dataclass
class LinkReport:
    findings: list[Finding] = field(default_factory=list)
    cited_source_ids: list[str] = field(default_factory=list)
    reference_source_ids: list[str] = field(default_factory=list)
    orphan_claim_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def cited_in_order(content: dict[str, Any]) -> list[str]:
    """Source ids cited in the text, each once, in order of first use. Walks nested nodes."""
    seen: dict[str, None] = {}
    stack: list[dict[str, Any]] = [content]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("type") == "cite":
            source_id = (node.get("attrs") or {}).get("source_id")
            if isinstance(source_id, str):
                seen.setdefault(source_id, None)
        children = node.get("content") or []
        stack.extend(reversed([c for c in children if isinstance(c, dict)]))
    return list(seen)


def check_section(content: dict[str, Any], claims: Iterable[ClaimFacts], sources: dict[str, SourceFacts]) -> LinkReport:
    report = LinkReport()
    cited = cited_in_order(content)
    report.cited_source_ids = cited
    cited_set = set(cited)

    for source_id in cited:
        source = sources.get(source_id)
        if source is None:
            report.findings.append(Finding("unknown_source", source_id, "Cited source is not an active source of this project"))
        elif not source.verified:
            report.findings.append(Finding("unverified_citation", source_id, f"Cited source is not verified: {source.title}"))
        else:
            report.reference_source_ids.append(source_id)

    for claim in claims:
        if not claim.evidence_source_ids:
            report.orphan_claim_ids.append(claim.claim_id)
            report.findings.append(Finding("orphan_claim", claim.claim_id, "Claim has no evidence linked"))
            continue
        for source_id in claim.evidence_source_ids:
            source = sources.get(source_id)
            if source is None or not source.verified:
                report.findings.append(Finding("unverified_evidence", claim.claim_id,
                                               "Claim evidence is not from a verified project source"))
            elif source_id not in cited_set:
                report.findings.append(Finding("evidence_not_cited", source_id,
                                               "Verified evidence for a claim in this section is never cited in the text"))
    return report
