"""Add PostgreSQL server timestamp defaults to search queries."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261029_0099"
down_revision: Union[str, Sequence[str], None] = "20261027_0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("search_queries") as batch:
        for column in ("created_at", "updated_at"):
            batch.alter_column(
                column, server_default=sa.func.now(),
                existing_type=sa.DateTime(timezone=True), existing_nullable=False,
            )


def downgrade() -> None:
    with op.batch_alter_table("search_queries") as batch:
        for column in ("updated_at", "created_at"):
            batch.alter_column(
                column, server_default=None,
                existing_type=sa.DateTime(timezone=True), existing_nullable=False,
            )
