"""Add missing server timestamps to criteria and seed rows."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261027_0098"
down_revision: Union[str, Sequence[str], None] = "20261022_0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("screening_criteria", "seed_papers"):
        op.alter_column(table, "created_at", server_default=sa.func.now(), existing_type=sa.DateTime(timezone=True), existing_nullable=False)
        op.alter_column(table, "updated_at", server_default=sa.func.now(), existing_type=sa.DateTime(timezone=True), existing_nullable=False)


def downgrade() -> None:
    for table in ("screening_criteria", "seed_papers"):
        op.alter_column(table, "updated_at", server_default=None, existing_type=sa.DateTime(timezone=True), existing_nullable=False)
        op.alter_column(table, "created_at", server_default=None, existing_type=sa.DateTime(timezone=True), existing_nullable=False)
