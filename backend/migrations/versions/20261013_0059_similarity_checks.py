"""Similarity check results against project sources (X.31.14).

Revision ID: 20261013_0059
Revises: 20261013_0058
Create Date: 2026-10-13

Additive: one new table, `similarity_checks`. Each row is one check of a saved section version; it holds
offsets, kinds, ids and scores, never text. The table is append-only, enforced by an ORM guard
(app/ledger_guard.py) plus database triggers installed here. Nothing existing changes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261013_0059"
down_revision: Union[str, Sequence[str], None] = "20261013_0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SIMILARITY_MESSAGE = "similarity_checks is append-only: rows cannot be updated or deleted"


def upgrade() -> None:
    op.create_table(
        "similarity_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("requested_by", sa.String(length=100), nullable=False),
        sa.Column("findings_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_similarity_checks_project_id", "similarity_checks", ["project_id"])
    op.create_index("ix_similarity_checks_document_id", "similarity_checks", ["document_id"])
    op.create_index("ix_similarity_checks_version_id", "similarity_checks", ["version_id"])
    _install_append_only("similarity_checks", SIMILARITY_MESSAGE)


def downgrade() -> None:
    # Refuse to drop recorded checks while any exist: they are the record of what was checked.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM similarity_checks")).scalar():
        raise RuntimeError("similarity_checks still has rows; refusing to drop the similarity record")

    _remove_append_only("similarity_checks")
    op.drop_index("ix_similarity_checks_version_id", table_name="similarity_checks")
    op.drop_index("ix_similarity_checks_document_id", table_name="similarity_checks")
    op.drop_index("ix_similarity_checks_project_id", table_name="similarity_checks")
    op.drop_table("similarity_checks")


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
