"""Add qual_excerpts and qual_suggestions: excerpt-level coding with model suggestions (M5.5.2).

Revision ID: 20261018_0075
Revises: 20261017_0074
Create Date: 2026-10-18

Nothing is backfilled. The downgrade refuses while any excerpt or suggestion exists, because accepted and rejected
suggestions are part of the coding record.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261018_0075"
down_revision: Union[str, Sequence[str], None] = "20261017_0074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qual_excerpts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_qual_excerpts_project_id"), "qual_excerpts", ["project_id"], unique=False)
    op.create_table(
        "qual_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("excerpt_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=200), nullable=False),
        sa.Column("is_new", sa.Boolean(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("decided_by", sa.String(length=100), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["excerpt_id"], ["qual_excerpts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_qual_suggestions_project_id"), "qual_suggestions", ["project_id"], unique=False)
    op.create_index(op.f("ix_qual_suggestions_excerpt_id"), "qual_suggestions", ["excerpt_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM qual_suggestions")).scalar_one()
    count += bind.execute(sa.text("SELECT COUNT(*) FROM qual_excerpts")).scalar_one()
    if count:
        raise RuntimeError(f"Refusing to drop qual_excerpts and qual_suggestions: {count} row(s) hold coding records. Export them first.")
    op.drop_index(op.f("ix_qual_suggestions_excerpt_id"), table_name="qual_suggestions")
    op.drop_index(op.f("ix_qual_suggestions_project_id"), table_name="qual_suggestions")
    op.drop_table("qual_suggestions")
    op.drop_index(op.f("ix_qual_excerpts_project_id"), table_name="qual_excerpts")
    op.drop_table("qual_excerpts")
