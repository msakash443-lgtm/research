"""Add `figures` and `figure_versions` (M8.3): a figure a project draws from an uploaded document, and its
append-only version history.

Revision ID: 20261124_0110
Revises: 20261124_0109
Create Date: 2026-11-24

Additive: two new tables. `figure_versions` is append-only, same two-layer pattern as interpretation_findings
(migration 20261019_0082): an ORM guard (app/ledger_guard.py) plus the database triggers installed here.
`figures` only names the figure, so it stays mutable.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261124_0110"
down_revision: Union[str, Sequence[str], None] = "20261124_0109"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSIONS_MESSAGE = "figure_versions is append-only: rows cannot be updated or deleted"


def upgrade() -> None:
    op.create_table(
        "figures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_document_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["uploaded_document_id"], ["uploaded_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_figures_project_id", "figures", ["project_id"])
    op.create_index("ix_figures_uploaded_document_id", "figures", ["uploaded_document_id"])

    op.create_table(
        "figure_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("figure_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=False),
        sa.Column("spec_hash", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("suggestion_id", sa.Uuid(), nullable=True),
        sa.Column("model_id", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=50), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["figure_id"], ["figures.id"]),
        sa.ForeignKeyConstraint(["suggestion_id"], ["ai_suggestions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("figure_id", "number", name="uq_figure_version_number"),
    )
    op.create_index("ix_figure_versions_project_id", "figure_versions", ["project_id"])
    op.create_index("ix_figure_versions_figure_id", "figure_versions", ["figure_id"])
    op.create_index("ix_figure_versions_suggestion_id", "figure_versions", ["suggestion_id"])
    _install_append_only("figure_versions", VERSIONS_MESSAGE)


def downgrade() -> None:
    # Refuse to drop recorded figure history while any exists: it is the record of what was drawn, by whom,
    # and from which accepted suggestion. Same convention as interpretation_findings (migration 20261019_0082).
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM figure_versions")).scalar():
        raise RuntimeError("figure_versions still has rows; refusing to drop the figure history")
    if bind.execute(sa.text("SELECT COUNT(*) FROM figures")).scalar():
        raise RuntimeError("figures still has rows; refusing to drop the figure records")

    _remove_append_only("figure_versions")
    op.drop_index("ix_figure_versions_suggestion_id", table_name="figure_versions")
    op.drop_index("ix_figure_versions_figure_id", table_name="figure_versions")
    op.drop_index("ix_figure_versions_project_id", table_name="figure_versions")
    op.drop_table("figure_versions")

    op.drop_index("ix_figures_uploaded_document_id", table_name="figures")
    op.drop_index("ix_figures_project_id", table_name="figures")
    op.drop_table("figures")


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
