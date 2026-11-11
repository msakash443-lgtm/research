"""Add missing server timestamps to criteria and seed rows.

PostgreSQL-only: `ALTER COLUMN ... SET DEFAULT` is not valid SQLite DDL, so a scratch SQLite chain
cannot pass this revision. The project runs on PostgreSQL only (X.178), and CI applies
`upgrade head`, `downgrade base` and `upgrade head` to a PostgreSQL service (X.152).
"""
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
