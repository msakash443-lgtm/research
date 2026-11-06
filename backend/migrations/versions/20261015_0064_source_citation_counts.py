"""Add citation counts to sources (M2.13).

Revision ID: 20261015_0064
Revises: 20261015_0063
Create Date: 2026-10-15

Additive: three nullable columns on `sources`. NULL means "unknown" (no service gave a count),
so existing rows need no backfill. Values come only from `PaperRecord.cited_by_count`, which is
validated as >= 0; no table-level check is added, so SQLite needs no table rebuild.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261015_0064"
down_revision: Union[str, Sequence[str], None] = "20261015_0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("citation_count", sa.Integer(), nullable=True))
    op.add_column("sources", sa.Column("citation_count_source", sa.String(50), nullable=True))
    op.add_column("sources", sa.Column("citation_count_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "citation_count_at")
    op.drop_column("sources", "citation_count_source")
    op.drop_column("sources", "citation_count")
