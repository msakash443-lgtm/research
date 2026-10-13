"""Per-item "never for this item" AI lock (X.46.3).

Revision ID: 20261012_0047
Revises: 20261012_0046
Create Date: 2026-10-12

Additive: one new table, `ai_item_locks`, one row per (project, target kind, target id) that no AI step may be
given. Nothing existing changes; with no rows, every AI step behaves as before.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261012_0047"
down_revision: Union[str, Sequence[str], None] = "20261012_0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_item_locks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("target_kind", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.String(length=100), nullable=False),
        sa.Column("locked_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "target_kind", "target_id", name="uq_ai_item_lock"),
    )
    op.create_index("ix_ai_item_locks_project_id", "ai_item_locks", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_item_locks_project_id", table_name="ai_item_locks")
    op.drop_table("ai_item_locks")
