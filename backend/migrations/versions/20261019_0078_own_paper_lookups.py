"""Add own_paper_lookups table (M2.14.1).

Revision ID: 20261019_0078
Revises: 20261018_0077
Create Date: 2026-10-19

Additive: one new table, `own_paper_lookups`. Each row is the terminal outcome of one DOI-only/
title-only own-paper lookup, resolved through the enabled lookup connectors by the
`own_paper_lookup` task; it is written once with a final status. The table is append-only, enforced
by an ORM guard (app/ledger_guard.py) plus database triggers installed here. Nothing existing changes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261019_0078"
down_revision: Union[str, Sequence[str], None] = "20261018_0077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OWN_PAPER_LOOKUPS_MESSAGE = "own_paper_lookups is append-only: rows cannot be updated or deleted"


def upgrade() -> None:
    op.create_table(
        "own_paper_lookups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("matched_connector", sa.String(length=60), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_own_paper_lookups_project_id", "own_paper_lookups", ["project_id"])
    op.create_index("ix_own_paper_lookups_task_id", "own_paper_lookups", ["task_id"], unique=True)
    _install_append_only("own_paper_lookups", OWN_PAPER_LOOKUPS_MESSAGE)


def downgrade() -> None:
    # Refuse to drop recorded lookups while any exist: they are the record of what was resolved and included.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM own_paper_lookups")).scalar():
        raise RuntimeError("own_paper_lookups still has rows; refusing to drop the lookup record")

    _remove_append_only("own_paper_lookups")
    op.drop_index("ix_own_paper_lookups_task_id", table_name="own_paper_lookups")
    op.drop_index("ix_own_paper_lookups_project_id", table_name="own_paper_lookups")
    op.drop_table("own_paper_lookups")


def _install_append_only(table: str, message: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {table}_append_only() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '{message}';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {table}_append_only()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {table}_append_only()"
        )
    elif dialect == "sqlite":
        for suffix, verb in (("no_update", "UPDATE"), ("no_delete", "DELETE")):
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS {table}_{suffix} BEFORE {verb} ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{message}');
                END
                """
            )


def _remove_append_only(table: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_append_only()")
    elif dialect == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
