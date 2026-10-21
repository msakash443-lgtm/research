"""Add qual_suggestions.model_id and prompt_version: the provenance of each AI code suggestion (X.121).

Revision ID: 20261022_0088
Revises: 20261020_0087
Create Date: 2026-10-22

Additive: two nullable columns. Rows stored before this revision stay NULL (the model and prompt were not kept);
nothing is backfilled, since a guessed value would be a fabricated record.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261022_0088"
down_revision: Union[str, Sequence[str], None] = "20261020_0087"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("qual_suggestions") as batch:
        batch.add_column(sa.Column("model_id", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("prompt_version", sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("qual_suggestions") as batch:
        batch.drop_column("prompt_version")
        batch.drop_column("model_id")
