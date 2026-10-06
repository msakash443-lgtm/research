"""Add created_by to context items, sources, source excerpts and research runs.

Revision ID: 20261003_0007
Revises: 20261003_0006
Create Date: 2026-10-03

`created_by` is a user id or `agent:<name>`. Rows that already exist keep NULL (creator
unknown): the history is not guessed at.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_0007"
down_revision: Union[str, Sequence[str], None] = "20261003_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("research_context_items", "sources", "source_excerpts", "research_runs")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("created_by", sa.String(length=100), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        # Plain DROP COLUMN (not a batch rebuild) so SQLite keeps the table's triggers.
        op.drop_column(table, "created_by")
