"""Let a context entry be amended (history kept) and retired (hidden, never deleted) (X.161)."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261029_0100"
down_revision: Union[str, Sequence[str], None] = "20261029_0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("research_context_items") as batch:
        batch.add_column(sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("retired_by", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("history", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("research_context_items") as batch:
        batch.drop_column("history")
        batch.drop_column("retired_by")
        batch.drop_column("retired_at")
