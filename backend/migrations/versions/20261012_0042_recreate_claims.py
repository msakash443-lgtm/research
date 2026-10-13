"""Recreate evidence-linked claims with a database verification guard.

Revision ID: 20261012_0042
Revises: 20261012_0041
Create Date: 2026-10-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.claim_guard import SQLITE_DELETE, SQLITE_INSERT, SQLITE_MOVE, SQLITE_VERIFY, POSTGRES_FUNCTION, POSTGRES_TRIGGERS


revision: str = "20261012_0042"
down_revision: Union[str, Sequence[str], None] = "20261012_0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), sa.ForeignKey("extractions.id")),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("research_runs.id")),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("strength", sa.String(30)),
        sa.Column("section_id", sa.Uuid()),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("project_id", "extraction_id", "run_id"):
        op.create_index(f"ix_claims_{column}", "claims", [column])
    op.create_table(
        "claim_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("claim_id", sa.Uuid(), sa.ForeignKey("claims.id"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("excerpt_id", sa.Uuid(), sa.ForeignKey("source_excerpts.id"), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.UniqueConstraint("claim_id", "source_id", name="uq_claim_evidence_source"),
    )
    for column in ("claim_id", "source_id", "excerpt_id"):
        op.create_index(f"ix_claim_evidence_{column}", "claim_evidence", [column])
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for statement in (SQLITE_VERIFY, SQLITE_INSERT, SQLITE_DELETE, SQLITE_MOVE):
            op.execute(sa.text(statement))
    elif dialect == "postgresql":
        op.execute(sa.text(POSTGRES_FUNCTION))
        for statement in POSTGRES_TRIGGERS:
            op.execute(sa.text(statement))


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM claims")):
        raise RuntimeError("Cannot downgrade claims while claim rows exist")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION claim_evidence_guard() CASCADE")
    op.drop_table("claim_evidence")
    op.drop_table("claims")
