"""Give research runs that are still queued or running a task, now that runs execute from the task queue.

Revision ID: 20261003_0012
Revises: 20261003_0011
Create Date: 2026-10-03

Without this, a run that was waiting (or mid-flight on a worker that is being replaced) when
the new worker starts would never be picked up. Finished runs are left alone. Downgrade
does not delete the tasks: they are harmless history.
"""

import uuid
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261003_0012"
down_revision: Union[str, Sequence[str], None] = "20261003_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TASK_STATUSES = ("queued", "running", "completed", "failed", "blocked", "paused")


def upgrade() -> None:
    if context.is_offline_mode():
        return  # data-only step; nothing to read when generating SQL
    bind = op.get_bind()
    runs = sa.table("research_runs", sa.column("id", sa.Uuid()), sa.column("project_id", sa.Uuid()))
    waiting = bind.execute(
        sa.select(runs.c.id, runs.c.project_id).where(sa.text("status IN ('queued', 'running')"))
    ).fetchall()
    if not waiting:
        return
    status_type = sa.Enum(*TASK_STATUSES, name="task_status").with_variant(
        postgresql.ENUM(*TASK_STATUSES, name="task_status", create_type=False), "postgresql"
    )
    tasks = sa.table(
        "tasks",
        sa.column("id", sa.Uuid()),
        sa.column("project_id", sa.Uuid()),
        sa.column("type", sa.String()),
        sa.column("payload", sa.JSON()),
        sa.column("status", status_type),
    )
    op.bulk_insert(
        tasks,
        [
            {
                "id": uuid.uuid4(),
                "project_id": row.project_id,
                "type": "research_run",
                "payload": {"run_id": str(row.id)},
                "status": "queued",
            }
            for row in waiting
        ],
    )


def downgrade() -> None:
    pass
