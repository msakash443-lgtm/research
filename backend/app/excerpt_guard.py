"""Enforce that evidence excerpts are immutable once written.

Two layers: an ORM guard (clear error before any SQL is sent) and a database trigger
(catches raw SQL and other writers). Only `content` and `content_hash` are protected;
`locator` and timestamps are not. Correcting an excerpt means adding a new one.
"""

from __future__ import annotations

from sqlalchemy import DDL, event, inspect

from app.models import SourceExcerpt

IMMUTABLE_MESSAGE = "source_excerpts.content and content_hash are immutable; add a new excerpt instead"

SQLITE_TRIGGER = f"""
CREATE TRIGGER IF NOT EXISTS source_excerpts_immutable
BEFORE UPDATE OF content, content_hash ON source_excerpts
WHEN NEW.content IS NOT OLD.content OR NEW.content_hash IS NOT OLD.content_hash
BEGIN
    SELECT RAISE(ABORT, '{IMMUTABLE_MESSAGE}');
END;
"""

POSTGRES_FUNCTION = f"""
CREATE OR REPLACE FUNCTION source_excerpts_immutable() RETURNS trigger AS $$
BEGIN
    IF NEW.content IS DISTINCT FROM OLD.content OR NEW.content_hash IS DISTINCT FROM OLD.content_hash THEN
        RAISE EXCEPTION '{IMMUTABLE_MESSAGE}';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

POSTGRES_TRIGGER = """
CREATE TRIGGER source_excerpts_immutable
BEFORE UPDATE ON source_excerpts
FOR EACH ROW EXECUTE FUNCTION source_excerpts_immutable();
"""


@event.listens_for(SourceExcerpt, "before_update")
def _reject_excerpt_edit(mapper, connection, target: SourceExcerpt) -> None:
    attrs = inspect(target).attrs
    if attrs.content.history.has_changes() or attrs.content_hash.history.has_changes():
        raise ValueError(IMMUTABLE_MESSAGE)


# Tables created by create_all (tests, local SQLite) get the trigger too. Alembic
# migrations install the same SQL for PostgreSQL.
_table = SourceExcerpt.__table__
event.listen(_table, "after_create", DDL(SQLITE_TRIGGER).execute_if(dialect="sqlite"))
event.listen(_table, "after_create", DDL(POSTGRES_FUNCTION).execute_if(dialect="postgresql"))
event.listen(_table, "after_create", DDL(POSTGRES_TRIGGER).execute_if(dialect="postgresql"))
