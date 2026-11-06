"""Make document_versions and authorship_events append-only.

Same two-layer pattern as `audit_guard.py` and `note_guard.py`: an ORM guard (clear error before any SQL)
and database triggers (catch raw SQL, bulk deletes and other writers). Saving a document writes a new
version and new authorship events; nothing already written is changed or removed. `documents` itself is
not guarded: its `current_version_id` moves with each save.
"""

from __future__ import annotations

from sqlalchemy import DDL, event

from app.models import (
    AuthorshipEvent,
    DocumentVersion,
    InterpretationFinding,
    InterpretationReview,
    OwnPaperLookup,
    ProvenanceReport,
    SectionApproval,
    SimilarityCheck,
)

VERSIONS_MESSAGE = "document_versions is append-only: rows cannot be updated or deleted"
EVENTS_MESSAGE = "authorship_events is append-only: rows cannot be updated or deleted"
SIMILARITY_MESSAGE = "similarity_checks is append-only: rows cannot be updated or deleted"
PROVENANCE_MESSAGE = "provenance_reports is append-only: rows cannot be updated or deleted"
OWN_PAPER_LOOKUPS_MESSAGE = "own_paper_lookups is append-only: rows cannot be updated or deleted"
SECTION_APPROVALS_MESSAGE = "section_approvals is append-only: rows cannot be updated or deleted"
INTERPRETATION_FINDINGS_MESSAGE = "interpretation_findings is append-only: rows cannot be updated or deleted"
INTERPRETATION_REVIEWS_MESSAGE = "interpretation_reviews is append-only: rows cannot be updated or deleted"


def _sqlite_triggers(table: str, message: str) -> list[str]:
    return [
        f"""
CREATE TRIGGER IF NOT EXISTS {table}_no_update
BEFORE UPDATE ON {table}
BEGIN
    SELECT RAISE(ABORT, '{message}');
END;
""",
        f"""
CREATE TRIGGER IF NOT EXISTS {table}_no_delete
BEFORE DELETE ON {table}
BEGIN
    SELECT RAISE(ABORT, '{message}');
END;
""",
    ]


def _postgres_function(table: str, message: str) -> str:
    return f"""
CREATE OR REPLACE FUNCTION {table}_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '{message}';
END;
$$ LANGUAGE plpgsql;
"""


def _postgres_triggers(table: str) -> list[str]:
    return [
        f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {table}_append_only()",
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {table}_append_only()",
    ]


APPEND_ONLY_TABLES = {
    "document_versions": (DocumentVersion, VERSIONS_MESSAGE),
    "authorship_events": (AuthorshipEvent, EVENTS_MESSAGE),
    "similarity_checks": (SimilarityCheck, SIMILARITY_MESSAGE),
    "provenance_reports": (ProvenanceReport, PROVENANCE_MESSAGE),
    "own_paper_lookups": (OwnPaperLookup, OWN_PAPER_LOOKUPS_MESSAGE),
    "section_approvals": (SectionApproval, SECTION_APPROVALS_MESSAGE),
    "interpretation_findings": (InterpretationFinding, INTERPRETATION_FINDINGS_MESSAGE),
    "interpretation_reviews": (InterpretationReview, INTERPRETATION_REVIEWS_MESSAGE),
}

SQLITE_TRIGGERS = {name: _sqlite_triggers(name, msg) for name, (_, msg) in APPEND_ONLY_TABLES.items()}
POSTGRES_FUNCTIONS = {name: _postgres_function(name, msg) for name, (_, msg) in APPEND_ONLY_TABLES.items()}
POSTGRES_TRIGGERS = {name: _postgres_triggers(name) for name in APPEND_ONLY_TABLES}


def _reject(mapper, connection, target) -> None:
    raise ValueError(APPEND_ONLY_TABLES[target.__table__.name][1])


for _name, (_model, _message) in APPEND_ONLY_TABLES.items():
    event.listen(_model, "before_update", _reject)
    event.listen(_model, "before_delete", _reject)

    # Tables built by create_all (tests, local SQLite) get the triggers too; Alembic installs
    # the same SQL for PostgreSQL.
    _table = _model.__table__
    for _sql in SQLITE_TRIGGERS[_name]:
        event.listen(_table, "after_create", DDL(_sql).execute_if(dialect="sqlite"))
    event.listen(_table, "after_create", DDL(POSTGRES_FUNCTIONS[_name]).execute_if(dialect="postgresql"))
    for _sql in POSTGRES_TRIGGERS[_name]:
        event.listen(_table, "after_create", DDL(_sql).execute_if(dialect="postgresql"))
