"""Composite index for the task claim query (M0.6.7).

Revision ID: 20261007_0033
Revises: 20261007_0032
"""
from typing import Sequence, Union

from alembic import op

revision: str = "20261007_0033"
down_revision: Union[str, Sequence[str], None] = "20261007_0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_tasks_claim", "tasks", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_tasks_claim", table_name="tasks")
