"""Add projects.{data_availability,conflict_of_interest,ethics}_statement (M6.5).

Revision ID: 20261018_0076
Revises: 20261018_0075
Create Date: 2026-10-18

Nullable, no backfill: the researcher's own submission-statement text for manuscript export; existing
projects have none yet, and the export route shows a bracketed to-complete marker for an unset one.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261018_0076"
down_revision: Union[str, Sequence[str], None] = "20261018_0075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("data_availability_statement", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("conflict_of_interest_statement", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("ethics_statement", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "ethics_statement")
    op.drop_column("projects", "conflict_of_interest_statement")
    op.drop_column("projects", "data_availability_statement")
