"""Add password authentication for local accounts."""

from alembic import op
import sqlalchemy as sa

revision = "20260910_0003"
down_revision = "20260910_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "password_hash")
