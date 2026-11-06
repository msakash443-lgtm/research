"""Store sampled second extraction and field agreement."""

from alembic import op
import sqlalchemy as sa

revision = "20261013_0051"
down_revision = "20261013_0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("extractions", sa.Column("agreement_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("extractions", "agreement_json")
