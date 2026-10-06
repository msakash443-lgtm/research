"""Merged duplicates: sources.merged_into.

Revision ID: 20261005_0023
Revises: 20261005_0022
Create Date: 2026-10-05

Additive nullable column; existing sources are not merged into anything.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261005_0023"
down_revision: Union[str, Sequence[str], None] = "20261005_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("merged_into", sa.Uuid(), nullable=True))
    op.create_index("ix_sources_merged_into", "sources", ["merged_into"])


def downgrade() -> None:
    op.drop_index("ix_sources_merged_into", table_name="sources")
    op.drop_column("sources", "merged_into")
