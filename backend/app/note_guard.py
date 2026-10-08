"""Make note_revisions append-only.

Same two-layer pattern as `audit_guard.py`: an ORM guard (clear error before any SQL)
and a database trigger (catches raw SQL and other writers). Editing a note writes a new
revision instead; nothing already written is changed or removed.
"""

from __future__ import annotations

from sqlalchemy import DDL, event

from app.models import NoteRevision

APPEND_ONLY_MESSAGE = "note_revisions is append-only: rows cannot be updated or deleted"

SQLITE_TRIGGERS = [
    f"""
CREATE TRIGGER IF NOT EXISTS note_revisions_no_update
BEFORE UPDATE ON note_revisions
BEGIN
    SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}');
END;
""",
    f"""
CREATE TRIGGER IF NOT EXISTS note_revisions_no_delete
BEFORE DELETE ON note_revisions
BEGIN
    SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}');
END;
""",
]

POSTGRES_FUNCTION = f"""
CREATE OR REPLACE FUNCTION note_revisions_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '{APPEND_ONLY_MESSAGE}';
END;
$$ LANGUAGE plpgsql;
"""

POSTGRES_TRIGGERS = [
    "CREATE TRIGGER note_revisions_no_update_delete BEFORE UPDATE OR DELETE ON note_revisions "
    "FOR EACH ROW EXECUTE FUNCTION note_revisions_append_only()",
    "CREATE TRIGGER note_revisions_no_truncate BEFORE TRUNCATE ON note_revisions "
    "FOR EACH STATEMENT EXECUTE FUNCTION note_revisions_append_only()",
]


@event.listens_for(NoteRevision, "before_update")
@event.listens_for(NoteRevision, "before_delete")
def _reject_note_revision_change(mapper, connection, target: NoteRevision) -> None:
    raise ValueError(APPEND_ONLY_MESSAGE)


# Tables built by create_all (tests, local SQLite) get the triggers too; Alembic installs
# the same SQL for PostgreSQL.
_table = NoteRevision.__table__
for _sql in SQLITE_TRIGGERS:
    event.listen(_table, "after_create", DDL(_sql).execute_if(dialect="sqlite"))
event.listen(_table, "after_create", DDL(POSTGRES_FUNCTION).execute_if(dialect="postgresql"))
for _sql in POSTGRES_TRIGGERS:
    event.listen(_table, "after_create", DDL(_sql).execute_if(dialect="postgresql"))
