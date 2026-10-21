"""Add sources.language: the language a connector reported for a work (X.86).

Revision ID: 20261020_0086
Revises: 20261020_0085
Create Date: 2026-10-20

Additive: one nullable two-letter column. Existing rows stay NULL (language was not kept), which the bias check
reports as "unknown", never as English.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261020_0086"
down_revision: Union[str, Sequence[str], None] = "20261020_0085"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sources") as batch:
        batch.add_column(sa.Column("language", sa.String(length=2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sources") as batch:
        batch.drop_column("language")
