"""Search log: the search_queries table.

Revision ID: 20261005_0021
Revises: 20261005_0020
Create Date: 2026-10-05

Additive; nothing writes to it yet (the search runner is M1.7.2).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261005_0021"
down_revision: Union[str, Sequence[str], None] = "20261005_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_queries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("search_id", sa.Uuid(), nullable=False),
        sa.Column("database", sa.String(length=50), nullable=False),
        sa.Column("query_string", sa.Text(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("n_results", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("search_id", "version", name="uq_search_query_version"),
        sa.CheckConstraint("version >= 1", name="ck_search_query_version_positive"),
        sa.CheckConstraint("n_results IS NULL OR n_results >= 0", name="ck_search_query_n_results"),
    )
    op.create_index("ix_search_queries_project_id", "search_queries", ["project_id"])
    op.create_index("ix_search_queries_search_id", "search_queries", ["search_id"])


def downgrade() -> None:
    op.drop_index("ix_search_queries_search_id", table_name="search_queries")
    op.drop_index("ix_search_queries_project_id", table_name="search_queries")
    op.drop_table("search_queries")
