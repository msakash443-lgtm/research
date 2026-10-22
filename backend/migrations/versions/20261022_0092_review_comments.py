"""Add structured review comments (M7.1).

Revision ID: 20261022_0092
Revises: 20261022_0091
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261022_0092"
down_revision: Union[str, Sequence[str], None] = "20261022_0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("persona", sa.String(length=60), nullable=False),
        sa.Column("severity", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="open", nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_comments_project_id", "review_comments", ["project_id"])
    op.create_index("ix_review_comments_document_id", "review_comments", ["document_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM review_comments")).scalar_one():
        raise RuntimeError("Refusing to drop review_comments while comments exist")
    op.drop_index("ix_review_comments_document_id", table_name="review_comments")
    op.drop_index("ix_review_comments_project_id", table_name="review_comments")
    op.drop_table("review_comments")
