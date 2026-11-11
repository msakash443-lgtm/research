"""Add projects.active_learning_enabled (M2.6, spec 5.3.2: rank unscreened papers by predicted relevance).

Revision ID: 20261010_0043
Revises: 20261010_0042
Create Date: 2026-10-10

Additive, off by default (rule 18): existing projects' queue order (oldest first) is unchanged until
an owner turns this on and asks for `sort=relevance`.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261010_0043"
down_revision: Union[str, Sequence[str], None] = "20261010_0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("active_learning_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("projects", "active_learning_enabled")
