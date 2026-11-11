"""Add exclusion_rules: case-exclusion rules that need human approval before they can drop rows (M5.7.1, spec 5.8 gate).

Revision ID: 20261017_0073
Revises: 20261017_0072
Create Date: 2026-10-17

Nothing is backfilled. The downgrade refuses while any rule exists, because an approved rule is part of the record of
why cases were excluded.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261017_0073"
down_revision: Union[str, Sequence[str], None] = "20261017_0072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "exclusion_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("rule_json", sa.JSON(), nullable=False),
        sa.Column("rule_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("proposed_by", sa.String(length=100), nullable=False),
        sa.Column("approved_by", sa.String(length=100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_exclusion_rules_project_id"), "exclusion_rules", ["project_id"], unique=False)
    op.create_index(op.f("ix_exclusion_rules_dataset_id"), "exclusion_rules", ["dataset_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM exclusion_rules")).scalar_one()
    if count:
        raise RuntimeError(f"Refusing to drop exclusion_rules: {count} rule(s) hold exclusion decisions. Export them first.")
    op.drop_index(op.f("ix_exclusion_rules_dataset_id"), table_name="exclusion_rules")
    op.drop_index(op.f("ix_exclusion_rules_project_id"), table_name="exclusion_rules")
    op.drop_table("exclusion_rules")
