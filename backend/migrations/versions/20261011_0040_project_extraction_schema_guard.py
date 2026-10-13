"""Prevent rewriting or deleting versioned project extraction schemas.

Revision ID: 20261011_0040
Revises: 20261012_0047
Create Date: 2026-10-11
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20261011_0040"
down_revision: Union[str, Sequence[str], None] = "20261012_0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MESSAGE = "project_extraction_schemas is append-only: rows cannot be updated or deleted"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION project_extraction_schemas_append_only() RETURNS trigger AS $$ "
            f"BEGIN RAISE EXCEPTION '{MESSAGE}'; END; $$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER project_extraction_schemas_no_update_delete "
            "BEFORE UPDATE OR DELETE ON project_extraction_schemas "
            "FOR EACH ROW EXECUTE FUNCTION project_extraction_schemas_append_only()"
        )
        op.execute(
            "CREATE TRIGGER project_extraction_schemas_no_truncate "
            "BEFORE TRUNCATE ON project_extraction_schemas "
            "FOR EACH STATEMENT EXECUTE FUNCTION project_extraction_schemas_append_only()"
        )
    elif bind.dialect.name == "sqlite":
        for name, verb in (
            ("project_extraction_schemas_no_update", "UPDATE"),
            ("project_extraction_schemas_no_delete", "DELETE"),
        ):
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS {name} BEFORE {verb} ON project_extraction_schemas
                BEGIN
                    SELECT RAISE(ABORT, '{MESSAGE}');
                END;
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS project_extraction_schemas_no_truncate ON project_extraction_schemas")
        op.execute("DROP TRIGGER IF EXISTS project_extraction_schemas_no_update_delete ON project_extraction_schemas")
        op.execute("DROP FUNCTION IF EXISTS project_extraction_schemas_append_only()")
    elif bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS project_extraction_schemas_no_delete")
        op.execute("DROP TRIGGER IF EXISTS project_extraction_schemas_no_update")
