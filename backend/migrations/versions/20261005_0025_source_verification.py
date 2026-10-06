"""How a source's metadata was verified: method, time, who, and the last automatic check.

Revision ID: 20261005_0025
Revises: 20261005_0024
Create Date: 2026-10-05

Additive nullable columns. Existing verified sources keep NULL method, which means "a person"
(before this migration only a person could verify).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261005_0025"
down_revision: Union[str, Sequence[str], None] = "20261005_0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("verification_method", sa.String(length=20), nullable=True))
    op.add_column("sources", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sources", sa.Column("verified_by", sa.String(length=100), nullable=True))
    op.add_column("sources", sa.Column("verification", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "verification")
    op.drop_column("sources", "verified_by")
    op.drop_column("sources", "verified_at")
    op.drop_column("sources", "verification_method")
