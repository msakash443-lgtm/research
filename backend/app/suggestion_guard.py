"""Make ai_suggestion_events append-only (X.46.1).

Same two-layer pattern as `note_guard.py`: an ORM guard (clear error before any SQL) and a database
trigger (catches raw SQL and other writers). Undoing or discarding a suggestion appends a new event;
nothing already written is changed or removed.
"""

from __future__ import annotations

from sqlalchemy import DDL, event

from app.models import AiSuggestionEvent

APPEND_ONLY_MESSAGE = "ai_suggestion_events is append-only: rows cannot be updated or deleted"

SQLITE_TRIGGERS = [
    f"""
CREATE TRIGGER IF NOT EXISTS ai_suggestion_events_no_update
BEFORE UPDATE ON ai_suggestion_events
BEGIN
    SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}');
END;
""",
    f"""
CREATE TRIGGER IF NOT EXISTS ai_suggestion_events_no_delete
BEFORE DELETE ON ai_suggestion_events
BEGIN
    SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}');
END;
""",
]

POSTGRES_FUNCTION = f"""
CREATE OR REPLACE FUNCTION ai_suggestion_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '{APPEND_ONLY_MESSAGE}';
END;
$$ LANGUAGE plpgsql;
"""

POSTGRES_TRIGGERS = [
    "CREATE TRIGGER ai_suggestion_events_no_update_delete BEFORE UPDATE OR DELETE ON ai_suggestion_events "
    "FOR EACH ROW EXECUTE FUNCTION ai_suggestion_events_append_only()",
    "CREATE TRIGGER ai_suggestion_events_no_truncate BEFORE TRUNCATE ON ai_suggestion_events "
    "FOR EACH STATEMENT EXECUTE FUNCTION ai_suggestion_events_append_only()",
]


@event.listens_for(AiSuggestionEvent, "before_update")
@event.listens_for(AiSuggestionEvent, "before_delete")
def _reject_suggestion_event_change(mapper, connection, target: AiSuggestionEvent) -> None:
    raise ValueError(APPEND_ONLY_MESSAGE)


# Tables built by create_all (tests, local SQLite) get the triggers too; Alembic installs
# the same SQL for PostgreSQL.
_table = AiSuggestionEvent.__table__
for _sql in SQLITE_TRIGGERS:
    event.listen(_table, "after_create", DDL(_sql).execute_if(dialect="sqlite"))
event.listen(_table, "after_create", DDL(POSTGRES_FUNCTION).execute_if(dialect="postgresql"))
for _sql in POSTGRES_TRIGGERS:
    event.listen(_table, "after_create", DDL(_sql).execute_if(dialect="postgresql"))
