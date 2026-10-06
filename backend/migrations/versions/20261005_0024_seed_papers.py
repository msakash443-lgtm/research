"""Seed papers for the known-item recall check: the seed_papers table.

Revision ID: 20261005_0024
Revises: 20261005_0023
Create Date: 2026-10-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261005_0024"
down_revision: Union[str, Sequence[str], None] = "20261005_0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seed_papers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("authors", sa.JSON(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_seed_papers_project_id", "seed_papers", ["project_id"])
    op.create_index("uq_seed_paper_doi", "seed_papers", ["project_id", "doi"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_seed_paper_doi", table_name="seed_papers")
    op.drop_index("ix_seed_papers_project_id", table_name="seed_papers")
    op.drop_table("seed_papers")
