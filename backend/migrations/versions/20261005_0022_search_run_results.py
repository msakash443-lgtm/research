"""Search runs: exactness, counts and the identified records on search_queries.

Revision ID: 20261005_0022
Revises: 20261005_0021
Create Date: 2026-10-05

Additive nullable columns; rows written before this migration (none exist outside tests) keep NULL.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261005_0022"
down_revision: Union[str, Sequence[str], None] = "20261005_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("search_queries", sa.Column("exact", sa.Boolean(), nullable=True))
    op.add_column("search_queries", sa.Column("caveats", sa.JSON(), nullable=True))
    op.add_column("search_queries", sa.Column("counts", sa.JSON(), nullable=True))
    op.add_column("search_queries", sa.Column("results", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("search_queries", "results")
    op.drop_column("search_queries", "counts")
    op.drop_column("search_queries", "caveats")
    op.drop_column("search_queries", "exact")
