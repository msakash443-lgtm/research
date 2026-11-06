"""Make project extraction schema versions append-only.

Schemas are versioned records: changing a schema must create a new row so existing
extractions retain the exact schema they were checked against.  ORM events provide
a clear application error and database triggers protect against raw SQL or bulk
writes.
"""

from __future__ import annotations

from sqlalchemy import DDL, event

from app.models import ProjectExtractionSchema

APPEND_ONLY_MESSAGE = "project_extraction_schemas is append-only: rows cannot be updated or deleted"

SQLITE_TRIGGERS = [
    f"""
CREATE TRIGGER IF NOT EXISTS project_extraction_schemas_no_update
BEFORE UPDATE ON project_extraction_schemas
BEGIN
    SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}');
END;
""",
    f"""
CREATE TRIGGER IF NOT EXISTS project_extraction_schemas_no_delete
BEFORE DELETE ON project_extraction_schemas
BEGIN
    SELECT RAISE(ABORT, '{APPEND_ONLY_MESSAGE}');
END;
""",
]

POSTGRES_FUNCTION = f"""
CREATE OR REPLACE FUNCTION project_extraction_schemas_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '{APPEND_ONLY_MESSAGE}';
END;
$$ LANGUAGE plpgsql;
"""

POSTGRES_TRIGGERS = [
    "CREATE TRIGGER project_extraction_schemas_no_update_delete BEFORE UPDATE OR DELETE ON project_extraction_schemas "
    "FOR EACH ROW EXECUTE FUNCTION project_extraction_schemas_append_only()",
    "CREATE TRIGGER project_extraction_schemas_no_truncate BEFORE TRUNCATE ON project_extraction_schemas "
    "FOR EACH STATEMENT EXECUTE FUNCTION project_extraction_schemas_append_only()",
]


@event.listens_for(ProjectExtractionSchema, "before_update")
@event.listens_for(ProjectExtractionSchema, "before_delete")
def _reject_project_schema_change(mapper, connection, target: ProjectExtractionSchema) -> None:
    raise ValueError(APPEND_ONLY_MESSAGE)


# Tables built by create_all (tests, local SQLite) get the triggers too; Alembic installs
# the same SQL for PostgreSQL.
_table = ProjectExtractionSchema.__table__
for _sql in SQLITE_TRIGGERS:
    event.listen(_table, "after_create", DDL(_sql).execute_if(dialect="sqlite"))
event.listen(_table, "after_create", DDL(POSTGRES_FUNCTION).execute_if(dialect="postgresql"))
for _sql in POSTGRES_TRIGGERS:
    event.listen(_table, "after_create", DDL(_sql).execute_if(dialect="postgresql"))
