"""Add section_approvals: one record per person's approval of a manuscript section's current text, with the
AI-involvement level frozen from its section provenance report (G10, M6.11, spec 5.11 / 8).

Revision ID: 20261019_0081
Revises: 20261019_0080
Create Date: 2026-10-19

Additive: one new table. Append-only, same two-layer pattern as provenance_reports (migration 20261013_0060):
an ORM guard (app/ledger_guard.py) plus database triggers installed here. Nothing existing changes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261019_0081"
down_revision: Union[str, Sequence[str], None] = "20261019_0080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SECTION_APPROVALS_MESSAGE = "section_approvals is append-only: rows cannot be updated or deleted"


def upgrade() -> None:
    op.create_table(
        "section_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("ai_share_pct", sa.Float(), nullable=False),
        sa.Column("ai_shares", sa.JSON(), nullable=False),
        sa.Column("approved_by", sa.String(length=100), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["provenance_reports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_section_approvals_project_id", "section_approvals", ["project_id"])
    op.create_index("ix_section_approvals_document_id", "section_approvals", ["document_id"])
    _install_append_only("section_approvals", SECTION_APPROVALS_MESSAGE)


def downgrade() -> None:
    # Refuse to drop recorded approvals while any exist: they are the record of who approved what text,
    # with which AI-involvement level, same convention as provenance_reports.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM section_approvals")).scalar():
        raise RuntimeError("section_approvals still has rows; refusing to drop the approval record")

    _remove_append_only("section_approvals")
    op.drop_index("ix_section_approvals_document_id", table_name="section_approvals")
    op.drop_index("ix_section_approvals_project_id", table_name="section_approvals")
    op.drop_table("section_approvals")


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
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_append_only()")
    elif dialect == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")
