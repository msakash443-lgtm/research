"""Add analysis_plans: locked-to-be analysis plans for one dataset version each (M5.3.1, spec 5.9).

Revision ID: 20261017_0072
Revises: 20261017_0071
Create Date: 2026-10-16

Nothing is backfilled. The downgrade refuses while any plan exists, because a locked plan is part of the record of
what was planned before results were seen.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261017_0072"
down_revision: Union[str, Sequence[str], None] = "20261017_0071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("environment_sha256", sa.String(length=64), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analysis_plans_project_id"), "analysis_plans", ["project_id"], unique=False)
    op.create_index(op.f("ix_analysis_plans_dataset_id"), "analysis_plans", ["dataset_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM analysis_plans")).scalar_one()
    if count:
        raise RuntimeError(f"Refusing to drop analysis_plans: {count} plan(s) hold planning records. Export them first.")
    op.drop_index(op.f("ix_analysis_plans_dataset_id"), table_name="analysis_plans")
    op.drop_index(op.f("ix_analysis_plans_project_id"), table_name="analysis_plans")
    op.drop_table("analysis_plans")
