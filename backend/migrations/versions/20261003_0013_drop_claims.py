"""Drop the unused claims and claim_evidence tables (plan X.21.9).

Nothing ever wrote to them. The upgrade refuses to run if either table has rows, so
real data is never dropped silently. Claim-evidence work (M6) will design its own schema.

Revision ID: 20261003_0013
Revises: 20261003_0012
Create Date: 2026-10-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_0013"
down_revision: Union[str, Sequence[str], None] = "20261003_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    if not op.get_context().as_sql:
        bind = op.get_bind()
        for table in ("claim_evidence", "claims"):
            if bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
                raise RuntimeError(f"{table} has rows; refusing to drop it. Export or delete them first.")

    op.drop_index("ix_claim_evidence_source_id", table_name="claim_evidence")
    op.drop_index("ix_claim_evidence_claim_id", table_name="claim_evidence")
    op.drop_table("claim_evidence")
    op.drop_index("ix_claims_project_id", table_name="claims")
    op.drop_table("claims")
    sa.Enum(name="claim_status").drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    claim_status = sa.Enum("supports", "contradicts", "qualifies", "unclear", name="claim_status")
    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", claim_status, nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_claims_project_id", "claims", ["project_id"])
    op.create_table(
        "claim_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("relationship", claim_status, nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("locator", sa.String(length=255), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_id", "source_id", name="uq_claim_source"),
    )
    op.create_index("ix_claim_evidence_claim_id", "claim_evidence", ["claim_id"])
    op.create_index("ix_claim_evidence_source_id", "claim_evidence", ["source_id"])
