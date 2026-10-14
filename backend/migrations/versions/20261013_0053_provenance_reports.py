"""Writing provenance reports (X.31.12).

Revision ID: 20261013_0053
Revises: 20261013_0052
Create Date: 2026-10-13

Additive: one new table, `provenance_reports`. Each row is one frozen report of the saved sections it covers. It holds
counts, ids, timestamps and the statement, never manuscript or source text. The table is append-only, enforced by an
ORM guard (app/ledger_guard.py) plus database triggers installed here. Nothing existing changes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261013_0053"
down_revision: Union[str, Sequence[str], None] = "20261013_0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROVENANCE_MESSAGE = "provenance_reports is append-only: rows cannot be updated or deleted"


def upgrade() -> None:
    op.create_table(
        "provenance_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=12), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("covered_versions", sa.JSON(), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("statement_text", sa.Text(), nullable=False),
        sa.Column("statement_template", sa.String(length=60), nullable=False),
        sa.Column("report_hash", sa.String(length=64), nullable=False),
        sa.Column("generated_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provenance_reports_project_id", "provenance_reports", ["project_id"])
    op.create_index("ix_provenance_reports_document_id", "provenance_reports", ["document_id"])
    _install_append_only("provenance_reports", PROVENANCE_MESSAGE)


def downgrade() -> None:
    # Refuse to drop recorded reports while any exist: they are what was given to supervisors and journals.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM provenance_reports")).scalar():
        raise RuntimeError("provenance_reports still has rows; refusing to drop the provenance record")

    _remove_append_only("provenance_reports")
    op.drop_index("ix_provenance_reports_document_id", table_name="provenance_reports")
    op.drop_index("ix_provenance_reports_project_id", table_name="provenance_reports")
    op.drop_table("provenance_reports")


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
