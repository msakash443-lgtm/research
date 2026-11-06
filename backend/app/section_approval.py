"""Per-section approval gate (G10, M6.11, spec 5.11 / 8: "Human gate: Scholar approves each section;
AI-involvement level is recorded per section.").

Approving a section freezes the AI-involvement numbers from its current, non-stale section provenance report
(`app/provenance_report.py`, scope `"section"`) onto one append-only `SectionApproval` row — nothing here
computes those numbers itself. G10, the project-wide gate (`app/gates.py`), can only be approved once every
current manuscript section has a current approval of its current saved version (`unapproved_documents`,
used by `routers/gates.py::decide_gate`). A section saved again after approval makes its approval stale
(`mark_stale`, called from the document save path alongside `provenance_store.mark_covering_reports_stale`),
so an out-of-date approval never counts toward G10.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.artifacts import register_artifact
from app.models import Artifact, ArtifactStatus, Document, Project, ProvenanceReport, SectionApproval, User, utcnow

ARTIFACT_KIND = "section_approval"
STALE_REASON = "section changed after approval"


class NoCurrentReport(Exception):
    """No non-stale section provenance report covers this document's current saved version."""


def _current_section_report(db: Session, document: Document) -> ProvenanceReport | None:
    """The newest `scope="section"` report for this document whose artifact is current and whose single
    covered version is the document's current saved version, or None."""
    if document.current_version_id is None:
        return None
    reports = db.scalars(
        select(ProvenanceReport)
        .where(ProvenanceReport.scope == "section", ProvenanceReport.document_id == document.id)
        .order_by(ProvenanceReport.created_at.desc())
    ).all()
    for report in reports:
        if not report.covered_versions or report.covered_versions[0]["version_id"] != str(document.current_version_id):
            continue
        artifact = db.scalar(
            select(Artifact).where(Artifact.kind == "provenance_report", Artifact.ref_id == str(report.id))
        )
        if artifact is not None and artifact.status == ArtifactStatus.current:
            return report
    return None


def approve(db: Session, project: Project, document: Document, *, user: User, note: str | None = None) -> SectionApproval:
    """Approve `document`'s current saved text. The caller commits. Raises `NoCurrentReport` if no current
    section provenance report covers the document's current saved version."""
    report = _current_section_report(db, document)
    if report is None:
        raise NoCurrentReport()
    actor = audit.user_actor(user)
    covered = report.covered_versions[0]
    row = SectionApproval(
        project_id=project.id, document_id=document.id, version_id=document.current_version_id,
        version_number=covered["number"], report_id=report.id,
        ai_share_pct=report.report_json["ai"]["share_pct"], ai_shares=report.report_json["shares"],
        approved_by=actor, note=note,
    )
    db.add(row)
    db.flush()
    register_artifact(db, project.id, ARTIFACT_KIND, row.id, project.stage, actor=actor)
    audit.record(
        db, actor=actor, action="section.approved", project_id=project.id,
        payload={
            "document_id": str(document.id), "version": row.version_number,
            "ai_share_pct": row.ai_share_pct, "report_id": str(report.id),
        },
    )
    return row


def artifact_for(db: Session, row: SectionApproval) -> Artifact | None:
    return db.scalar(select(Artifact).where(Artifact.kind == ARTIFACT_KIND, Artifact.ref_id == str(row.id)))


def current_approval(db: Session, document: Document) -> SectionApproval | None:
    """The newest current (non-stale) approval of this document, or None if it has never been approved or
    the section was saved again since its last approval."""
    rows = db.scalars(
        select(SectionApproval).where(SectionApproval.document_id == document.id).order_by(SectionApproval.created_at.desc())
    ).all()
    for row in rows:
        artifact = artifact_for(db, row)
        if artifact is not None and artifact.status == ArtifactStatus.current:
            return row
    return None


def mark_stale(db: Session, project_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """A document was saved again: its current approval, if any, no longer describes the saved text.
    Nothing is committed here; the caller's save transaction commits it."""
    artifacts = db.scalars(
        select(Artifact).where(Artifact.project_id == project_id, Artifact.kind == ARTIFACT_KIND, Artifact.status == ArtifactStatus.current)
    ).all()
    if not artifacts:
        return
    rows = {
        str(row.id): row
        for row in db.scalars(select(SectionApproval).where(SectionApproval.id.in_([uuid.UUID(a.ref_id) for a in artifacts]))).all()
    }
    for artifact in artifacts:
        row = rows.get(artifact.ref_id)
        if row is not None and row.document_id == document_id:
            artifact.status = ArtifactStatus.stale
            artifact.stale_reason = STALE_REASON
            artifact.stale_at = utcnow()


@dataclass
class UnapprovedSection:
    document_id: uuid.UUID
    title: str
    reason: str  # "unsaved" (nothing saved yet) | "unapproved" (no current approval of the current version)


def unapproved_documents(db: Session, project: Project) -> list[UnapprovedSection]:
    """Every manuscript section without a current approval of its current saved version, in manuscript order."""
    documents = db.scalars(
        select(Document).where(Document.project_id == project.id).order_by(Document.position, Document.created_at)
    ).all()
    unapproved: list[UnapprovedSection] = []
    for document in documents:
        if document.current_version_id is None:
            unapproved.append(UnapprovedSection(document.id, document.title, "unsaved"))
            continue
        if current_approval(db, document) is None:
            unapproved.append(UnapprovedSection(document.id, document.title, "unapproved"))
    return unapproved
