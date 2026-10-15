"""Add the researcher brief (M2.12).

Revision ID: 20261015_0063
Revises: 20261014_0062
Create Date: 2026-10-15

Additive: one new table, `research_briefs`, one row per project.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261015_0063"
down_revision: Union[str, Sequence[str], None] = "20261014_0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_briefs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False, unique=True),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("keyword_groups", sa.JSON(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("min_citations", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("min_citations IS NULL OR min_citations >= 0", name="ck_research_brief_min_citations"),
    )


def downgrade() -> None:
    op.drop_table("research_briefs")
