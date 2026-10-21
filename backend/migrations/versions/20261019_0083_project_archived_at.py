"""Add projects.archived_at: the reversible archive state transition (X.4.7.3, Q23).

Revision ID: 20261019_0083
Revises: 20261019_0082
Create Date: 2026-10-19

Additive: one nullable column. Existing projects are not archived (NULL).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261019_0083"
down_revision: Union[str, Sequence[str], None] = "20261019_0082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("archived_at")
