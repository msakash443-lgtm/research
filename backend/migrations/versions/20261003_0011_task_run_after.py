"""Add tasks.run_after (retry backoff: do not claim before this time).

Revision ID: 20261003_0011
Revises: 20261003_0010
Create Date: 2026-10-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_0011"
down_revision: Union[str, Sequence[str], None] = "20261003_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("run_after", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "run_after")
