"""Optional titles for notes, used by explicit assistant note captures."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261123_0106"
down_revision: Union[str, Sequence[str], None] = "20261123_0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notes", sa.Column("title", sa.String(200), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM notes WHERE title IS NOT NULL)" )).scalar():
        raise RuntimeError("note titles exist; refusing destructive downgrade")
    op.drop_column("notes", "title")
