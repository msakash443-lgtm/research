"""Record when each task attempt started and when the task ended (X.7.1).

Revision ID: 20261022_0090
Revises: 20261022_0089
Create Date: 2026-10-22

Additive: two nullable columns on `tasks`. Existing rows stay NULL, so durations are only
reported for tasks that ran after this migration. Downgrade drops the columns and with them
any recorded durations; it does not refuse, because the values are derived timings, not records
of decisions.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261022_0090"
down_revision: Union[str, Sequence[str], None] = "20261022_0089"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "finished_at")
    op.drop_column("tasks", "started_at")
