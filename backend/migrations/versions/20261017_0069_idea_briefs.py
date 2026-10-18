"""Add idea_briefs: the scholar's idea brief for gate G1 (M4.7, spec 5.1).

Revision ID: 20261017_0069
Revises: 20261015_0066
Create Date: 2026-10-16

One row per project, created on first save. Nothing is backfilled: existing projects simply have no brief.
The downgrade refuses while any brief exists, because dropping the table would discard the scholar's words.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261017_0069"
down_revision: Union[str, Sequence[str], None] = "20261017_0068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "idea_briefs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("why_it_matters", sa.Text(), nullable=False),
        sa.Column("risks", sa.Text(), nullable=True),
        sa.Column("selected_idea_id", sa.Uuid(), nullable=True),
        sa.Column("feasibility", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["selected_idea_id"], ["research_context_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM idea_briefs")).scalar_one()
    if count:
        raise RuntimeError(f"Refusing to drop idea_briefs: {count} brief(s) hold the scholar's text. Export them first.")
    op.drop_table("idea_briefs")
