"""Add use_web_retrieval to research_runs.

Revision ID: 20261001_0002
Revises: 20260907_0001
Create Date: 2026-10-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261001_0002"
down_revision: Union[str, Sequence[str], None] = "20260907_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("use_web_retrieval", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("research_runs", "use_web_retrieval")
