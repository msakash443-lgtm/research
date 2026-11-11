"""Project extraction schemas: scholar-edited, versioned copies of extraction schemas (M3.1.2).

Revision ID: 20261007_0034
Revises: 20261007_0033
Create Date: 2026-10-07

Additive. Rows are immutable; an edit adds the next version.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261007_0034"
down_revision: Union[str, Sequence[str], None] = "20261007_0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_extraction_schemas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("based_on", sa.String(length=80), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_project_extraction_schema_version_positive"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", "version", name="uq_project_extraction_schema_version"),
    )
    op.create_index("ix_project_extraction_schemas_project_id", "project_extraction_schemas", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_project_extraction_schemas_project_id", table_name="project_extraction_schemas")
    op.drop_table("project_extraction_schemas")
