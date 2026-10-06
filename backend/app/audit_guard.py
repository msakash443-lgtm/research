"""Make audit_events append-only.

Two layers, as for excerpts: ORM events (clear error before any SQL) and database
triggers (catch raw SQL, bulk deletes and other writers). There is also no API route
that updates or deletes events. A database owner can still drop the trigger or the
table; this guards the application and ordinary writers, not a hostile DBA.
"""

from __future__ import annotations

from sqlalchemy import DDL, event

from app.models import AuditEvent

APPEND_ONLY_MESSAGE = "audit_events is append-only: rows cannot be updated or deleted"

SQLITE_TRIGGERS = [
    f"""
CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}');
END;
""",
    f"""
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}');
END;
""",
]

POSTGRES_FUNCTION = f"""
CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '{APPEND_ONLY_MESSAGE}';
END;
$$ LANGUAGE plpgsql;
"""

POSTGRES_TRIGGERS = [
    "CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events "
    "FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()",
    "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
    "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()",
]


@event.listens_for(AuditEvent, "before_update")
@event.listens_for(AuditEvent, "before_delete")
def _reject_audit_change(mapper, connection, target: AuditEvent) -> None:
    raise ValueError(APPEND_ONLY_MESSAGE)


# Tables built by create_all (tests, local SQLite) get the triggers too; Alembic installs
# the same SQL for PostgreSQL.
_table = AuditEvent.__table__
for _sql in SQLITE_TRIGGERS:
    event.listen(_table, "after_create", DDL(_sql).execute_if(dialect="sqlite"))
event.listen(_table, "after_create", DDL(POSTGRES_FUNCTION).execute_if(dialect="postgresql"))
for _sql in POSTGRES_TRIGGERS:
    event.listen(_table, "after_create", DDL(_sql).execute_if(dialect="postgresql"))
