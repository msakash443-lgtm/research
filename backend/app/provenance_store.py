"""Database side of the writing provenance report (X.31.12).

`generate` reads the latest saved version of each covered section, hands plain spans and version history to the pure
rules in `app/provenance_report.py`, and writes one append-only `ProvenanceReport` with its Artifact and audit row.
`mark_covering_reports_stale` runs inside the save transaction, so a covered section saved again makes its report stale
in the same commit. Nothing here calls a model.
"""

from __future__ import annotations

import uuid
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, ledger, provenance_report as pr
from app.artifacts import register_artifact
from app.models import AiSuggestion, Artifact, ArtifactStatus, Document, DocumentVersion, ProvenanceReport, Project, User, utcnow

ARTIFACT_KIND = "provenance_report"
STALE_REASON = "section changed after the report"


class NothingSaved(Exception):
    """None of the requested sections has a saved version yet."""


def _suggestion_meta(db: Session, spans: Sequence[ledger.Span]) -> dict[str, dict[str, Any]]:
    ids = set()
    for span in spans:
        if span.origin in ledger.AI_ORIGINS and span.sug:
            try:
                ids.add(uuid.UUID(span.sug))
            except ValueError:
                continue
    if not ids:
        return {}
    rows = db.scalars(select(AiSuggestion).where(AiSuggestion.id.in_(ids))).all()
    return {str(row.id): {"model_id": row.model_id, "prompt_version": row.prompt_version, "action": row.action} for row in rows}


def generate(
    db: Session,
    project: Project,
    *,
    user: User,
    scope: str,
    documents: Sequence[Document],
    document_id: uuid.UUID | None,
) -> ProvenanceReport:
    """Write one report over the latest saved version of each document. The caller commits."""
    covered: list[tuple[Document, DocumentVersion]] = []
    for document in documents:
        if document.current_version_id is None:
            continue
        version = db.get(DocumentVersion, document.current_version_id)
        if version is not None:
            covered.append((document, version))
    if not covered:
        raise NothingSaved()

    per_section = [(document, version, list(ledger.collect_spans(version.content_json).values())) for document, version in covered]
    meta = _suggestion_meta(db, [span for _, _, spans in per_section for span in spans])
    span_rows: list[dict[str, Any]] = []
    version_rows: list[dict[str, Any]] = []
    for document, version, spans in per_section:
        history = db.scalars(
            select(DocumentVersion).where(DocumentVersion.document_id == document.id, DocumentVersion.number <= version.number)
        ).all()
        version_rows += [
            {"document_id": str(document.id), "client_session_id": row.client_session_id, "created_at": row.created_at}
            for row in history
        ]
        for span in spans:
            info = meta.get(span.sug or "", {})
            span_rows.append({
                "document_id": str(document.id), "span_id": span.sid, "chars": len(span.text), "origin": span.origin,
                "paste": span.paste, "source_id": span.src, "suggestion_id": span.sug if span.origin in ledger.AI_ORIGINS else None,
                "model_id": info.get("model_id"), "prompt_version": info.get("prompt_version"), "action": info.get("action"),
            })

    covered_rows = [
        {"document_id": str(document.id), "title": document.title, "version_id": str(version.id),
         "number": version.number, "content_hash": version.content_hash}
        for document, version in covered
    ]
    report_json = pr.build_report(scope, covered_rows, span_rows, version_rows)
    template = pr.load_template()
    statement = pr.render_statement(report_json, template)
    label = pr.template_label(template)
    actor = audit.user_actor(user)

    row = ProvenanceReport(
        project_id=project.id, scope=scope, document_id=document_id, covered_versions=covered_rows,
        report_json=report_json, statement_text=statement, statement_template=label,
        report_hash=pr.report_hash(report_json, statement), generated_by=actor,
    )
    db.add(row)
    db.flush()
    register_artifact(db, project.id, ARTIFACT_KIND, row.id, project.stage, actor=actor)
    audit.record(db, actor=actor, action="provenance.generated", project_id=project.id,
                 payload={"report_id": str(row.id), "scope": scope, "sections": len(covered_rows),
                          "statement_template": label, "report_hash": row.report_hash})
    return row


def artifact_for(db: Session, report: ProvenanceReport) -> Artifact | None:
    return db.scalar(select(Artifact).where(Artifact.kind == ARTIFACT_KIND, Artifact.ref_id == str(report.id)))


def mark_covering_reports_stale(db: Session, project_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """A covered section was saved again: its report no longer describes the current text. Nothing is committed here."""
    artifacts = db.scalars(
        select(Artifact).where(Artifact.project_id == project_id, Artifact.kind == ARTIFACT_KIND, Artifact.status == ArtifactStatus.current)
    ).all()
    if not artifacts:
        return
    reports = {
        str(report.id): report
        for report in db.scalars(select(ProvenanceReport).where(ProvenanceReport.id.in_([uuid.UUID(a.ref_id) for a in artifacts]))).all()
    }
    for artifact in artifacts:
        report = reports.get(artifact.ref_id)
        if report is not None and any(c["document_id"] == str(document_id) for c in report.covered_versions):
            artifact.status = ArtifactStatus.stale
            artifact.stale_reason = STALE_REASON
            artifact.stale_at = utcnow()
