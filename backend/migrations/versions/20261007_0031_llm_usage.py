"""Model token usage (M0.9.1).

Revision ID: 20261007_0031
Revises: 20261007_0030
Create Date: 2026-10-07

Additive: one new table. `project_id` is a plain column, not a foreign key, so spend history survives
deleting a project.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261007_0031"
down_revision: Union[str, Sequence[str], None] = "20261007_0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("purpose", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_llm_usage_project_id", "llm_usage", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_project_id", table_name="llm_usage")
    op.drop_table("llm_usage")
