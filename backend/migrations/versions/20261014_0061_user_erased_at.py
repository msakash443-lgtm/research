"""Mark pseudonymised accounts (X.4.7.1).

Revision ID: 20261014_0061
Revises: 20261013_0060
Create Date: 2026-10-14

Additive: one nullable column, `users.erased_at`. Existing users are not erased (NULL).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261014_0061"
down_revision: Union[str, Sequence[str], None] = "20261013_0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("erased_at")
