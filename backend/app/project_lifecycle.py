"""Project archive and deletion (X.4.7.3, Q23: owner signs off alone, no DPO review).

Archive is a reversible, owner-only, read-only state transition: `Project.archived_at` is set and
`project_access` (`app/dependencies.py`) refuses every write but the archive/unarchive/delete routes
themselves with 409 until the project is unarchived. Nothing is removed or rewritten.

Hard deletion is owner-only and removes every row this project owns plus its stored objects
(`ObjectStore.delete`, X.4.7.2) -- except the tables the database guards as append-only
(`app/audit_guard.py`, `app/note_guard.py`, `app/ledger_guard.py`, `app/suggestion_guard.py`,
`app/project_schema_guard.py`). A project cannot legitimately lose its own audit trail, and those
guards would raise `ABORT`/`RAISE EXCEPTION` on any attempt to delete their rows anyway. The decision
(Q23, 2026-10-19, owner via chat, no DPO review): refuse the whole deletion up front, cleanly, and
say exactly which append-only table still holds rows for this project, rather than build a second,
inconsistent "tombstone" or pseudonymisation path for projects. A project with real activity (every
project created through the normal API has at least one `audit_events` row from `project.created`)
can therefore never be hard-deleted; archiving is the only way to retire it. A project constructed
directly in the database with no recorded activity -- the only realistic case in tests -- can be.

`llm_usage` is deliberately left out of the cascade: its own docstring says spend history outlives a
project, the same way it outlives an erased user (`app/erasure.py`).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.database import Base
from app.models import (
    AiSuggestion,
    AiSuggestionEvent,
    AuditEvent,
    AuthorshipEvent,
    Claim,
    ClaimEvidence,
    Document,
    DocumentVersion,
    InterpretationFinding,
    Note,
    NoteAttachment,
    NoteRevision,
    OwnPaperLookup,
    Project,
    ProjectExtractionSchema,
    ProvenanceReport,
    SectionApproval,
    SimilarityCheck,
    Source,
    SourceExcerpt,
    User,
    utcnow,
)
from app.object_storage import get_object_store


class ArchiveBlocked(Exception):
    """The project cannot be archived or unarchived right now; the message says why."""


class DeletionBlocked(Exception):
    """The project cannot be hard-deleted right now; the message says why."""


# Append-only guarded tables with their own `project_id` column (direct children).
_DIRECT_APPEND_ONLY_TABLES: dict[str, type] = {
    "audit_events": AuditEvent,
    "project_extraction_schemas": ProjectExtractionSchema,
    "authorship_events": AuthorshipEvent,
    "similarity_checks": SimilarityCheck,
    "provenance_reports": ProvenanceReport,
    "interpretation_findings": InterpretationFinding,
    "own_paper_lookups": OwnPaperLookup,
    "section_approvals": SectionApproval,
}


def _append_only_blockers(db: Session, project_id: uuid.UUID) -> list[str]:
    """Names of append-only guarded tables that still hold a row for this project, if any."""
    blockers = [
        name
        for name, model in _DIRECT_APPEND_ONLY_TABLES.items()
        if db.scalar(select(model.id).where(model.project_id == project_id).limit(1)) is not None
    ]
    if (
        db.scalar(
            select(NoteRevision.id)
            .join(Note, Note.id == NoteRevision.note_id)
            .where(Note.project_id == project_id)
            .limit(1)
        )
        is not None
    ):
        blockers.append("note_revisions")
    if (
        db.scalar(
            select(DocumentVersion.id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.project_id == project_id)
            .limit(1)
        )
        is not None
    ):
        blockers.append("document_versions")
    if (
        db.scalar(
            select(AiSuggestionEvent.id)
            .join(AiSuggestion, AiSuggestion.id == AiSuggestionEvent.suggestion_id)
            .where(AiSuggestion.project_id == project_id)
            .limit(1)
        )
        is not None
    ):
        blockers.append("ai_suggestion_events")
    return blockers


def archive_project(db: Session, project: Project, user: User) -> None:
    """Owner-only, reversible: mark the project read-only. Commits nothing; the caller commits."""
    if project.archived_at is not None:
        raise ArchiveBlocked("Project is already archived")
    project.archived_at = utcnow()
    audit.record(db, actor=audit.user_actor(user), action="project.archived", project_id=project.id, payload={})


def unarchive_project(db: Session, project: Project, user: User) -> None:
    """Owner-only: restore write access to an archived project. Commits nothing; the caller commits."""
    if project.archived_at is None:
        raise ArchiveBlocked("Project is not archived")
    project.archived_at = None
    audit.record(db, actor=audit.user_actor(user), action="project.unarchived", project_id=project.id, payload={})


def _delete_stored_objects(db: Session, project_id: uuid.UUID) -> None:
    store = get_object_store(get_settings())
    paths = list(
        db.scalars(select(Source.fulltext_path).where(Source.project_id == project_id, Source.fulltext_path.isnot(None)))
    )
    paths += list(
        db.scalars(
            select(NoteAttachment.path).join(Note, Note.id == NoteAttachment.note_id).where(Note.project_id == project_id)
        )
    )
    # `delete` already treats a missing object as done, so any error here is a real storage failure.
    # It propagates before any row is removed, leaving the project intact so the owner can retry.
    for path in paths:
        store.delete(path)


# Indirect children have no `project_id` column of their own; they are removed by joining through
# their project-scoped parent, deepest first, before that parent row is deleted by the generic
# cascade below. By the time this runs, `_append_only_blockers` has already guaranteed
# `note_revisions`, `document_versions` and `ai_suggestion_events` are empty for this project.
def _delete_indirect_children(db: Session, project_id: uuid.UUID) -> None:
    db.execute(delete(ClaimEvidence).where(ClaimEvidence.claim_id.in_(select(Claim.id).where(Claim.project_id == project_id))))
    db.execute(delete(NoteAttachment).where(NoteAttachment.note_id.in_(select(Note.id).where(Note.project_id == project_id))))
    db.execute(delete(NoteRevision).where(NoteRevision.note_id.in_(select(Note.id).where(Note.project_id == project_id))))
    db.execute(
        delete(DocumentVersion).where(DocumentVersion.document_id.in_(select(Document.id).where(Document.project_id == project_id)))
    )
    db.execute(
        delete(AiSuggestionEvent).where(
            AiSuggestionEvent.suggestion_id.in_(select(AiSuggestion.id).where(AiSuggestion.project_id == project_id))
        )
    )
    db.execute(delete(SourceExcerpt).where(SourceExcerpt.source_id.in_(select(Source.id).where(Source.project_id == project_id))))


# `audit_events` keeps its audit trail even for a deleted project (its `project_id` is a plain
# indexed column, not an FK, exactly so this can survive); `llm_usage` outlives a project the same
# way it outlives an erased user. Neither is cascaded here.
_EXCLUDED_FROM_CASCADE = {"audit_events", "llm_usage", "projects"}


def delete_project(db: Session, project: Project, user: User) -> None:
    """Owner-only hard delete. Refuses with `DeletionBlocked` while any append-only-guarded row for
    this project exists; otherwise removes stored objects via `ObjectStore.delete`, every other row
    this project owns, and finally the project itself. Commits nothing; the caller commits."""
    blockers = _append_only_blockers(db, project.id)
    if blockers:
        raise DeletionBlocked(
            "Project cannot be deleted: it has append-only audit/history rows in "
            + ", ".join(sorted(blockers))
            + ". Archive the project instead; hard deletion is only possible for a project with no recorded activity."
        )
    project_id, title = project.id, project.title
    _delete_stored_objects(db, project_id)
    _delete_indirect_children(db, project_id)
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in _EXCLUDED_FROM_CASCADE or "project_id" not in table.c:
            continue
        db.execute(delete(table).where(table.c.project_id == project_id))
    audit.record(
        db, actor=audit.user_actor(user), action="project.deleted", project_id=project_id,
        payload={"project_id": str(project_id), "title": title},
    )
    db.delete(project)
