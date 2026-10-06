"""Add the general task queue table.

Revision ID: 20261003_0010
Revises: 20261003_0009
Create Date: 2026-10-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261003_0010"
down_revision: Union[str, Sequence[str], None] = "20261003_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TASK_STATUSES = ("queued", "running", "completed", "failed", "blocked", "paused")
GATE_CODES = tuple(f"G{n}" for n in range(1, 12))


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*TASK_STATUSES, name="task_status").create(bind, checkfirst=True)
    status_type = sa.Enum(*TASK_STATUSES, name="task_status").with_variant(
        postgresql.ENUM(*TASK_STATUSES, name="task_status", create_type=False), "postgresql"
    )
    # `gate_code` already exists (created with the gates table); reuse it without re-creating the type.
    gate_code = sa.Enum(*GATE_CODES, name="gate_code").with_variant(
        postgresql.ENUM(*GATE_CODES, name="gate_code", create_type=False), "postgresql"
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", status_type, nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_by_gate", gate_code, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_tasks_max_attempts_positive"),
        sa.CheckConstraint("status != 'blocked' OR blocked_by_gate IS NOT NULL", name="ck_tasks_blocked_names_gate"),
    )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])
    op.create_index("ix_tasks_type", "tasks", ["type"])
    op.create_index("ix_tasks_status", "tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_type", table_name="tasks")
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    op.drop_table("tasks")
    sa.Enum(name="task_status").drop(op.get_bind(), checkfirst=True)
