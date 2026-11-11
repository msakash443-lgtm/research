"""Citation and number guard for manuscript export (M6.4, M6.3).

`app.claim_linker.check_section` (M6.2) reports, per section, citations that name a source this project doesn't
know (`unknown_source`) or hasn't verified (`unverified_citation`), plus claim-completeness findings
(`orphan_claim`, `unverified_evidence`, `evidence_not_cited`). `app.number_citation.check_section` (M6.3) reports
inserted numbers that name an analysis run this project doesn't have (`unknown_run`) or whose path doesn't resolve
to a number in that run's stored output (`unresolved_number`). M6.2 left every finding report-only
(`routers/documents.py::section_link_check`); this module decides which of them block an export outright.

Scope: the citation and number findings block (plan rule 24, "citations come from verified records" / "numbers
come from code, not language" — an export must never carry a citation to a source nobody has verified, or a number
that is not actually computed). Orphan claims and uncited verified evidence are a claim-completeness concern, not
a fabrication leaving the project, so they stay report-only; a researcher sees them on `/link-check` before it
ever reaches export.

`section_report` is the single DB-aware fetch+check used by both the link-check endpoint and this guard, so the two
agree by construction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import claim_linker, number_citation
from app.models import AnalysisRun, Claim, Document, Source

BLOCKING_KINDS = frozenset({"unknown_source", "unverified_citation", "unknown_run", "unresolved_number"})


def _uuid_or_none(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def section_report(db: Session, project_id: uuid.UUID, document: Document, content: dict[str, Any]) -> claim_linker.LinkReport:
    """The claim/citation/number report for one section's saved content."""
    claims = db.scalars(
        select(Claim).where(Claim.project_id == project_id, Claim.section_id == document.id).order_by(Claim.created_at, Claim.id)
    ).all()
    facts = [
        claim_linker.ClaimFacts(claim_id=str(claim.id), verified=claim.verified,
                                evidence_source_ids=tuple(str(item.source_id) for item in claim.evidence))
        for claim in claims
    ]
    cited = claim_linker.cited_in_order(content)
    wanted = {source_id for fact in facts for source_id in fact.evidence_source_ids} | set(cited)
    wanted_ids = [parsed for parsed in (_uuid_or_none(value) for value in wanted) if parsed is not None]
    rows = (
        db.scalars(select(Source).where(Source.project_id == project_id, Source.merged_into.is_(None), Source.id.in_(wanted_ids))).all()
        if wanted_ids
        else []
    )
    sources = {str(row.id): claim_linker.SourceFacts(id=str(row.id), verified=bool(row.metadata_verified), title=row.title) for row in rows}
    report = claim_linker.check_section(content, facts, sources)

    run_ids = {run_id for run_id, _ in number_citation.stat_refs_in_order(content)}
    run_wanted_ids = [parsed for parsed in (_uuid_or_none(value) for value in run_ids) if parsed is not None]
    run_rows = (
        db.scalars(select(AnalysisRun).where(AnalysisRun.project_id == project_id, AnalysisRun.id.in_(run_wanted_ids))).all()
        if run_wanted_ids
        else []
    )
    runs = {str(row.id): number_citation.RunFacts(id=str(row.id), output=row.output_json) for row in run_rows}
    report.findings.extend(number_citation.check_section(content, runs).findings)
    return report


@dataclass(frozen=True)
class BlockingFinding:
    document_id: str
    document_title: str
    kind: str
    subject: str
    message: str
    # Why an unverified citation is unverifiable: the citation verifier's last verdict (`mismatch`, `not_found`,
    # `unavailable`, `incomplete`) or `not_checked` when no check was ever stored. None for other kinds.
    verification: str | None = None


def verification_verdict(verification: dict[str, Any] | None) -> str:
    """The stored citation-verifier verdict of a source, or `not_checked` when it was never checked."""
    verdict = (verification or {}).get("verdict")
    return verdict if isinstance(verdict, str) and verdict else "not_checked"


def export_blockers(db: Session, project_id: uuid.UUID, sections: list[tuple[Document, dict[str, Any]]]) -> list[BlockingFinding]:
    """Blocking citation findings across every saved section, in document order."""
    out: list[BlockingFinding] = []
    for document, content in sections:
        report = section_report(db, project_id, document, content)
        for finding in report.findings:
            if finding.kind in BLOCKING_KINDS:
                verification = None
                if finding.kind == "unverified_citation":
                    source_id = _uuid_or_none(finding.subject)
                    source = db.get(Source, source_id) if source_id is not None else None
                    verification = verification_verdict(source.verification if source is not None else None)
                out.append(
                    BlockingFinding(
                        document_id=str(document.id), document_title=document.title,
                        kind=finding.kind, subject=finding.subject, message=finding.message, verification=verification,
                    )
                )
    return out
