"""DB storage for the qualitative codebook (M5.5.2, storage slice).

`app/qual_coding.py` is the single source of truth for the accept/rename/merge rules (name validation, no
silent drops, cycle-safe resolution). This module is the only writer of `QualCode` rows: it loads the
project's codebook into that module's in-memory shape, runs the pure operation (which raises on an invalid
name so nothing bad is ever persisted), then writes the resulting row(s) and an audit event in the same
transaction as the caller. A renamed-away or merged-away row keeps its definition forever (only `redirect_to`
changes) — nothing is dropped, and the audit payload holds code names and ids only, never definition text.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, qual_coding
from app.models import Project, QualCode, User


def _load_state(db: Session, project_id: uuid.UUID) -> dict:
    rows = db.scalars(select(QualCode).where(QualCode.project_id == project_id)).all()
    codes = {row.name: row.definition or "" for row in rows if row.redirect_to is None}
    merged = {row.name: row.redirect_to for row in rows if row.redirect_to is not None}
    return {"codes": codes, "merged": merged, "log": []}


def active_codes(db: Session, project_id: uuid.UUID) -> dict[str, str]:
    """The current codebook: live code name -> definition."""
    return _load_state(db, project_id)["codes"]


def accept_code(db: Session, project: Project, user: User, name: str, definition: str) -> bool:
    """Add `name` to the codebook if it is not already known. A name already in use (active, renamed-away or
    merged-away) is treated as reuse: no new row, no event. Raises `ValueError` only for an invalid name/actor
    (empty name, for example) via `qual_coding.accept`."""
    state = _load_state(db, project.id)
    if name in state["codes"] or name in state["merged"]:
        return False
    actor = audit.user_actor(user)
    qual_coding.accept(state, name, definition, actor)
    db.add(QualCode(project_id=project.id, name=name, definition=definition, created_by=actor))
    audit.record(db, actor=actor, action="qual.code_added", project_id=project.id, payload={"code": name})
    return True


def rename_code(db: Session, project: Project, user: User, old: str, new: str) -> None:
    """Rename `old` to `new`. `old`'s row is kept (definition untouched) with `redirect_to` set to `new`, so
    `old` can never be reused for a different code and its definition is never lost."""
    state = _load_state(db, project.id)
    actor = audit.user_actor(user)
    qual_coding.rename(state, old, new, actor)  # raises ValueError if `old` is unknown or `new` is taken
    old_row = db.scalars(
        select(QualCode).where(QualCode.project_id == project.id, QualCode.name == old)
    ).one()
    db.add(QualCode(project_id=project.id, name=new, definition=old_row.definition, created_by=actor))
    old_row.redirect_to = new
    audit.record(db, actor=actor, action="qual.code_renamed", project_id=project.id, payload={"from": old, "to": new})


def merge_code(db: Session, project: Project, user: User, source: str, target: str) -> None:
    """Merge `source` into `target`. `source`'s row is kept (definition untouched) with `redirect_to` set to
    `target`; its definition is never lost, only no longer current."""
    state = _load_state(db, project.id)
    actor = audit.user_actor(user)
    qual_coding.merge(state, source, target, actor)  # raises ValueError if either name is unknown, or source == target
    source_row = db.scalars(
        select(QualCode).where(QualCode.project_id == project.id, QualCode.name == source)
    ).one()
    source_row.redirect_to = target
    audit.record(db, actor=actor, action="qual.code_merged", project_id=project.id,
                 payload={"from": source, "into": target})
