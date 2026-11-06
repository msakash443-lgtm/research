"""Store explicitly supplied bibliometric references and keywords."""

from alembic import op
import sqlalchemy as sa

revision = "20261013_0053"
down_revision = "20261013_0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("bibliometric_reference_ids", sa.JSON(), nullable=True))
    op.add_column("sources", sa.Column("bibliometric_keywords", sa.JSON(), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    count = connection.execute(sa.text(
        "SELECT COUNT(*) FROM sources WHERE bibliometric_reference_ids IS NOT NULL "
        "OR bibliometric_keywords IS NOT NULL"
    )).scalar_one()
    if count:
        raise RuntimeError("Cannot discard stored bibliometric inputs")
    op.drop_column("sources", "bibliometric_keywords")
    op.drop_column("sources", "bibliometric_reference_ids")
