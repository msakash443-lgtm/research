"""Reject UPDATE and DELETE on audit_events with database triggers.

Revision ID: 20261003_0006
Revises: 20261002_0005
Create Date: 2026-10-03
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20261003_0006"
down_revision: Union[str, Sequence[str], None] = "20261002_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MESSAGE = "audit_events is append-only: rows cannot be updated or deleted"


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '{MESSAGE}';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            "CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events "
            "FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()"
        )
        op.execute(
            "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
            "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()"
        )
    elif dialect == "sqlite":
        for name, verb in (("audit_events_no_update", "UPDATE"), ("audit_events_no_delete", "DELETE")):
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS {name} BEFORE {verb} ON audit_events
                BEGIN
                    SELECT RAISE(ABORT, '{MESSAGE}');
                END
                """
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events")
        op.execute("DROP FUNCTION IF EXISTS audit_events_append_only()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete")
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
