"""Extend sources toward the spec's Paper: venue, abstract, oa_url, source_ids, fulltext_path, quality_flags.

Revision ID: 20261003_0016
Revises: 20261003_0015
Create Date: 2026-10-03

All columns are nullable, so existing sources are unchanged.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_0016"
down_revision: Union[str, Sequence[str], None] = "20261003_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("venue", sa.String(length=500)),
    ("abstract", sa.Text()),
    ("oa_url", sa.String(length=2000)),
    ("source_ids", sa.JSON()),
    ("fulltext_path", sa.String(length=1000)),
    ("quality_flags", sa.JSON()),
)


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column("sources", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column("sources", name)
