"""Drop the unused projects.status column (plan M0.5.12).

Nothing reads or writes it: `Project.stage` (M0.5.1) replaced it in the workflow and
M0.5.7 already removed `status` from `ProjectRead`/the UI. It was always "active" (no
archive feature exists), so the upgrade refuses to run if any row has a different value.

Revision ID: 20261009_0038
Revises: 20261008_0037
Create Date: 2026-10-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261009_0038"
down_revision: Union[str, Sequence[str], None] = "20261008_0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not op.get_context().as_sql:
        bind = op.get_bind()
        unexpected = bind.execute(
            sa.text("SELECT COUNT(*) FROM projects WHERE status IS NULL OR status != 'active'")
        ).scalar()
        if unexpected:
            raise RuntimeError(
                "projects.status has rows other than 'active'; refusing to drop it. "
                "Resolve them first (the column was never meant to hold anything else)."
            )

    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("status")


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(length=40), nullable=False, server_default="active")
        )
