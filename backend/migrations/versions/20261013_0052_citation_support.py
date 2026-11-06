"""Track verbatim quote checks and explicit human support confirmation."""

from alembic import op
import sqlalchemy as sa

from app.claim_guard import SQLITE_DELETE, SQLITE_INSERT, SQLITE_MOVE, SQLITE_VERIFY

revision = "20261013_0052"
down_revision = "20261013_0051"
branch_labels = None
depends_on = None


def _without_sqlite_claim_guards(alter) -> None:
    # SQLite rebuilds the table for batch ALTER. Remove the claim guards first;
    # otherwise SQLite refuses to rename the temporary table because the
    # triggers still reference the old claim_evidence table. Restore them after.
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        for name in (
            "claims_require_evidence_verify",
            "claims_require_evidence_insert",
            "claims_keep_evidence_delete",
            "claims_keep_evidence_move",
        ):
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS {name}"))
    with op.batch_alter_table("claim_evidence") as batch:
        alter(batch)
    if sqlite:
        for statement in (SQLITE_VERIFY, SQLITE_INSERT, SQLITE_DELETE, SQLITE_MOVE):
            op.execute(sa.text(statement))


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
    _without_sqlite_claim_guards(_add_support_columns)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM claim_evidence WHERE support_confirmed_by IS NOT NULL")).scalar_one():
        raise RuntimeError("Cannot discard human citation-support confirmations")
    _without_sqlite_claim_guards(_drop_support_columns)
