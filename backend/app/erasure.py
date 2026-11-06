"""Account erasure by pseudonymisation (X.4.7.1, provisional Q23 basis).

The person's identifying data is removed; the `users` row and its id stay, so the append-only audit log and
every other record that names the user by id remain intact and attributable to "an erased user". Audit notes and
reasons are not rewritten (the log is append-only); they are kept under the research-integrity exemption.

Their private, never-filed notes (X.4.7.5) are scrubbed too: nobody but the owner could ever see them, so
erasure clears them rather than keeping them indefinitely. `note_revisions` is append-only like the audit log
(`app/note_guard.py`), so an older revision's text cannot be deleted — only the note's current state is cleared
and a final blank revision recorded, and attachments are removed from object storage.
"""

from __future__ import annotations

import logging
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.models import ExternalIdentity, Note, NoteRevision, ProjectMember, ProjectRole, User, utcnow
from app.object_storage import ObjectStorageError, get_object_store

logger = logging.getLogger(__name__)

ERASED_EMAIL_DOMAIN = "erased.invalid"


class ErasureBlocked(Exception):
    """The account cannot be erased yet; the message says what the person must do first."""


def _sole_owner_of_shared_project(db: Session, user: User) -> bool:
    """True if the user is the only owner of a project that has other members."""
    owned = select(ProjectMember.project_id).where(
        ProjectMember.user_id == user.id, ProjectMember.role == ProjectRole.owner
    )
    for project_id in db.scalars(owned):
        owners = db.scalar(
            select(func.count(ProjectMember.id)).where(
                ProjectMember.project_id == project_id, ProjectMember.role == ProjectRole.owner
            )
        )
        members = db.scalar(select(func.count(ProjectMember.id)).where(ProjectMember.project_id == project_id))
        if owners <= 1 and members > 1:
            return True
    return False


def _scrub_unfiled_notes(db: Session, user: User) -> int:
    """Clear the person's private notes that were never filed to a project (nobody else could see
    them). Older `NoteRevision` rows keep their text (append-only, cannot be deleted); the note's
    current body/quoted text/source URL are blanked, attachments are removed from storage, and one
    final revision records the scrub. Returns the number of notes scrubbed."""
    notes = db.scalars(select(Note).where(Note.owner_id == user.id, Note.project_id.is_(None))).all()
    store = get_object_store(get_settings())
    for note in notes:
        for attachment in list(note.attachments):
            try:
                store.delete(attachment.path)
            except ObjectStorageError:
                # Best-effort: the row below still drops the reference even if the object is gone. Logged (X.145).
                logger.warning("Could not delete stored object for note attachment %s during account erasure", attachment.id)
            db.delete(attachment)
        note.body = ""
        note.quoted_text = None
        note.source_url = None
        note.revision += 1
        db.add(NoteRevision(note_id=note.id, revision=note.revision, body="", quoted_text=None, edited_by=audit.user_actor(user)))
    return len(notes)


def erase_account(db: Session, user: User) -> None:
    """Pseudonymise `user` and end all their sessions. Commits nothing; the caller commits."""
    if user.erased_at is not None:
        raise ErasureBlocked("This account has already been erased")
    if _sole_owner_of_shared_project(db, user):
        raise ErasureBlocked("Hand over ownership of your shared projects to another owner before erasing your account")
    user.email = f"erased-{user.id.hex}@{ERASED_EMAIL_DOMAIN}"
    user.display_name = None
    user.erased_at = utcnow()
    user.session_epoch += 1
    db.execute(delete(ExternalIdentity).where(ExternalIdentity.user_id == user.id))
    scrubbed = _scrub_unfiled_notes(db, user)
    audit.record(db, actor=audit.user_actor(user), action="user.erased", payload={"user_id": str(user.id)})
    if scrubbed:
        audit.record(
            db, actor=audit.user_actor(user), action="user.unfiled_notes_scrubbed",
            payload={"user_id": str(user.id), "note_count": scrubbed},
        )
