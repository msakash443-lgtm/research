"""Add structured review issues and response metadata (M7.3).

Revision ID: 20261022_0093
Revises: 20261022_0092
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261022_0093"
down_revision: Union[str, Sequence[str], None] = "20261022_0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("severity", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="open", nullable=False),
        sa.Column("assignee", sa.String(length=100), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("redline_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_issues_project_id", "review_issues", ["project_id"])
    op.create_index("ix_review_issues_document_id", "review_issues", ["document_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM review_issues")).scalar_one():
        raise RuntimeError("Refusing to drop review_issues while rows exist")
    op.drop_index("ix_review_issues_document_id", table_name="review_issues")
    op.drop_index("ix_review_issues_project_id", table_name="review_issues")
    op.drop_table("review_issues")
