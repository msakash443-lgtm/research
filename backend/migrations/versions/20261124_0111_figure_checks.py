"""Add `figure_checks` (M8.6): one AI accuracy check of a figure version, append-only.

Revision ID: 20261124_0111
Revises: 20261124_0110
Create Date: 2026-11-24

Additive: one new table. Same two-layer pattern as figure_versions (migration 20261124_0110): an ORM guard
(app/ledger_guard.py) plus the database triggers installed here. A check is a record of what a model said about a
version; it is never edited or removed.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261124_0111"
down_revision: Union[str, Sequence[str], None] = "20261124_0110"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECKS_MESSAGE = "figure_checks is append-only: rows cannot be updated or deleted"


def upgrade() -> None:
    op.create_table(
        "figure_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("figure_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("labels_json", sa.JSON(), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=50), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["figure_version_id"], ["figure_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_figure_checks_project_id", "figure_checks", ["project_id"])
    op.create_index("ix_figure_checks_figure_version_id", "figure_checks", ["figure_version_id"])
    _install_append_only("figure_checks", CHECKS_MESSAGE)


def downgrade() -> None:
    # Refuse to drop recorded checks while any exists: they are the record of what the model said. Same convention
    # as figure_versions (migration 20261124_0110).
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM figure_checks")).scalar():
        raise RuntimeError("figure_checks still has rows; refusing to drop the check history")

    _remove_append_only("figure_checks")
    op.drop_index("ix_figure_checks_figure_version_id", table_name="figure_checks")
    op.drop_index("ix_figure_checks_project_id", table_name="figure_checks")
    op.drop_table("figure_checks")


def _install_append_only(table: str, message: str) -> None:
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


def _remove_append_only(table: str) -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
    op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
    op.execute(f"DROP FUNCTION IF EXISTS {table}_append_only()")
