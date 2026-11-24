"""Add `uploaded_documents` (M8.1): PDFs and DOCX files a person uploads for the research-figures work."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261124_0109"
down_revision: Union[str, Sequence[str], None] = "20261119_0108"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "uploaded_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("uploaded_by", sa.String(length=100), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_uploaded_documents_project_id", "uploaded_documents", ["project_id"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM uploaded_documents)")).scalar():
        raise RuntimeError("uploaded_documents still has rows; refusing to drop it")
    op.drop_index("ix_uploaded_documents_project_id", table_name="uploaded_documents")
    op.drop_table("uploaded_documents")
