"""Track verbatim quote checks and explicit human support confirmation."""

from alembic import op
import sqlalchemy as sa

revision = "20261013_0052"
down_revision = "20261013_0051"
branch_labels = None
depends_on = None


def _add_support_columns(batch) -> None:
    batch.add_column(sa.Column("quote_matches_source", sa.Boolean(), nullable=False, server_default=sa.false()))
    batch.add_column(sa.Column("support_confirmed_by", sa.String(100), nullable=True))
    batch.add_column(sa.Column("support_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    batch.create_check_constraint("ck_claim_evidence_support_confirmation_paired",
                                  "(support_confirmed_by IS NULL) = (support_confirmed_at IS NULL)")


def _drop_support_columns(batch) -> None:
    batch.drop_constraint("ck_claim_evidence_support_confirmation_paired", type_="check")
    batch.drop_column("support_confirmed_at")
    batch.drop_column("support_confirmed_by")
    batch.drop_column("quote_matches_source")


def upgrade() -> None:
    with op.batch_alter_table("claim_evidence") as batch:
        _add_support_columns(batch)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM claim_evidence WHERE support_confirmed_by IS NOT NULL")).scalar_one():
        raise RuntimeError("Cannot discard human citation-support confirmations")
    with op.batch_alter_table("claim_evidence") as batch:
        _drop_support_columns(batch)
