"""Screening criteria: projects.criteria_framework and the screening_criteria table.

Revision ID: 20261005_0020
Revises: 20261004_0019
Create Date: 2026-10-05

Both are additive; existing projects have no framework and no criteria.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261005_0020"
down_revision: Union[str, Sequence[str], None] = "20261004_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("criteria_framework", sa.String(length=20), nullable=True))
    op.create_table(
        "screening_criteria",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("element", sa.String(length=40), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "code", name="uq_criterion_code"),
    )
    op.create_index("ix_screening_criteria_project_id", "screening_criteria", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_screening_criteria_project_id", table_name="screening_criteria")
    op.drop_table("screening_criteria")
    op.drop_column("projects", "criteria_framework")
