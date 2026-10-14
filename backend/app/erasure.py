"""Account erasure by pseudonymisation (X.4.7.1, provisional Q23 basis).

The person's identifying data is removed; the `users` row and its id stay, so the append-only audit log and
every other record that names the user by id remain intact and attributable to "an erased user". Audit notes and
reasons are not rewritten (the log is append-only); they are kept under the research-integrity exemption.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import audit
from app.models import ExternalIdentity, ProjectMember, ProjectRole, User, utcnow

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
    audit.record(db, actor=audit.user_actor(user), action="user.erased", payload={"user_id": str(user.id)})
