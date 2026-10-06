"""Reject UPDATEs of source_excerpts.content / content_hash with a database trigger.

Revision ID: 20261002_0003
Revises: 20261001_0002
Create Date: 2026-10-02
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20261002_0003"
down_revision: Union[str, Sequence[str], None] = "20261001_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MESSAGE = "source_excerpts.content and content_hash are immutable; add a new excerpt instead"


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION source_excerpts_immutable() RETURNS trigger AS $$
            BEGIN
                IF NEW.content IS DISTINCT FROM OLD.content OR NEW.content_hash IS DISTINCT FROM OLD.content_hash THEN
                    RAISE EXCEPTION '{MESSAGE}';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            "CREATE TRIGGER source_excerpts_immutable BEFORE UPDATE ON source_excerpts "
            "FOR EACH ROW EXECUTE FUNCTION source_excerpts_immutable()"
        )
    elif dialect == "sqlite":
        op.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS source_excerpts_immutable
            BEFORE UPDATE OF content, content_hash ON source_excerpts
            WHEN NEW.content IS NOT OLD.content OR NEW.content_hash IS NOT OLD.content_hash
            BEGIN
                SELECT RAISE(ABORT, '{MESSAGE}');
            END
            """
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS source_excerpts_immutable ON source_excerpts")
        op.execute("DROP FUNCTION IF EXISTS source_excerpts_immutable()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS source_excerpts_immutable")
