"""Per-user session epoch for server-side sign-out (X.4.3).

Revision ID: 20261013_0056
Revises: 20261012_0047
Create Date: 2026-10-13

Additive: one column, `users.session_epoch`, default 0. Existing browser sessions carry no epoch and count as 0,
so nobody is signed out by this migration; a user's sessions end only when they use "sign out everywhere".
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261013_0056"
down_revision: Union[str, Sequence[str], None] = "20261012_0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("session_epoch")
