"""Personal-data export for one user: an access / portability request (X.4.8).

Everything here is data that is about the person themselves: their profile, sign-in identities, the projects they
belong to and their role, their own notes with every revision, and the audit events they caused. Other people's
data is never included: not other members' emails, not other people's notes, not project content. Project
content is exported through the project replication package (X.8.1), which a project owner can run. Stored files
(note attachments) are listed by name, type, size and hash, not embedded.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import audit
from app.models import AuditEvent, ExternalIdentity, Note, Project, ProjectMember, ProjectRole, User, utcnow

EXPORT_FORMAT_VERSION = 1


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _projects(db: Session, user: User) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Project)
        .outerjoin(ProjectMember, ProjectMember.project_id == Project.id)
        .where(or_(Project.owner_id == user.id, ProjectMember.user_id == user.id))
        .order_by(Project.created_at, Project.id)
    ).unique()
    roles = {m.project_id: m for m in db.scalars(select(ProjectMember).where(ProjectMember.user_id == user.id))}
    out = []
    for project in rows:
        member = roles.get(project.id)
        role = ProjectRole.owner.value if project.owner_id == user.id else member.role.value
        out.append(
            {
                "id": str(project.id),
                "title": project.title,
                "role": role,
                "credit_roles": list(member.credit_roles or []) if member is not None else [],
            }
        )
    return out


def _notes(db: Session, user: User) -> list[dict[str, Any]]:
    notes = db.scalars(select(Note).where(Note.owner_id == user.id).order_by(Note.captured_at, Note.id)).all()
    return [
        {
            "id": str(note.id),
            "project_id": str(note.project_id) if note.project_id else None,
            "kind": note.kind.value,
            "body": note.body,
            "quoted_text": note.quoted_text,
            "source_url": note.source_url,
            "locator": note.locator,
            "status": note.status.value,
            "captured_at": _iso(note.captured_at),
            "revisions": [
                {"revision": r.revision, "body": r.body, "quoted_text": r.quoted_text, "created_at": _iso(r.created_at)}
                for r in note.revisions
            ],
            "attachments": [
                {"media_type": a.media_type, "size": a.size, "sha256": a.sha256} for a in note.attachments
            ],
        }
        for note in notes
    ]


def _audit_events(db: Session, user: User) -> list[dict[str, Any]]:
    events = db.scalars(
        select(AuditEvent).where(AuditEvent.actor == audit.user_actor(user)).order_by(AuditEvent.timestamp, AuditEvent.id)
    ).all()
    return [
        {
            "id": str(e.id),
            "project_id": str(e.project_id) if e.project_id else None,
            "actor": e.actor,
            "action": e.action,
            "payload": e.payload_json,
            "timestamp": _iso(e.timestamp),
        }
        for e in events
    ]


def export_personal_data(db: Session, user: User) -> dict[str, Any]:
    """Build the export and record `user.data_exported`. Commits nothing; the caller commits."""
    data = {
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": utcnow().isoformat(),
        "profile": {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "created_at": _iso(user.created_at),
        },
        "external_identities": [
            {"provider": i.provider, "subject": i.subject}
            for i in db.scalars(select(ExternalIdentity).where(ExternalIdentity.user_id == user.id).order_by(ExternalIdentity.provider))
        ],
        "projects": _projects(db, user),
        "notes": _notes(db, user),
        "audit_events": _audit_events(db, user),
    }
    audit.record(db, actor=audit.user_actor(user), action="user.data_exported", payload={"user_id": str(user.id)})
    return data
