"""Add extractions.ai_fields_json: the AI's original values, kept after a person edits (X.80).

Revision ID: 20261020_0085
Revises: 20261020_0084
Create Date: 2026-10-20

Additive: one nullable JSON column. Existing rows stay NULL (the AI's first values were not kept, so those rows
cannot be scored in the accuracy report); new AI extractions fill it when they are created.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261020_0085"
down_revision: Union[str, Sequence[str], None] = "20261020_0084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("extractions") as batch:
        batch.add_column(sa.Column("ai_fields_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("extractions") as batch:
        batch.drop_column("ai_fields_json")
