"""Add project datasets for backend analysis.

Revision ID: 20260910_0002
Revises: 20260907_0001
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260910_0002"
down_revision: Union[str, Sequence[str], None] = "20260907_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=10), nullable=False),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_datasets_project_id", "datasets", ["project_id"])
    op.create_table(
        "literature_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_literature_documents_project_id", "literature_documents", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_literature_documents_project_id", table_name="literature_documents")
    op.drop_table("literature_documents")
    op.drop_index("ix_datasets_project_id", table_name="datasets")
    op.drop_table("datasets")
