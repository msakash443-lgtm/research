"""Source.fulltext_access: outcome of the last open-access full-text check (M2.7.1).

Revision ID: 20261008_0035
Revises: 20261007_0034
Create Date: 2026-10-08

Additive, nullable JSON column; set by the system only.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261008_0035"
down_revision: Union[str, Sequence[str], None] = "20261007_0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("fulltext_access", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "fulltext_access")
