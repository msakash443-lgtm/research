"""Add analysis_runs: one sandboxed run of a locked analysis plan each (M5.3.3, spec 5.9).

Revision ID: 20261017_0074
Revises: 20261017_0073
Create Date: 2026-10-17

Nothing is backfilled. The downgrade refuses while any run exists, because a run is the record of what was computed.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261017_0074"
down_revision: Union[str, Sequence[str], None] = "20261017_0073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_plan_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("dataset_sha256", sa.String(length=64), nullable=False),
        sa.Column("script_sha256", sa.String(length=64), nullable=False),
        sa.Column("environment_sha256", sa.String(length=64), nullable=False),
        sa.Column("image", sa.String(length=300), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["analysis_plan_id"], ["analysis_plans.id"]),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("project_id", "analysis_plan_id", "dataset_id"):
        op.create_index(op.f(f"ix_analysis_runs_{column}"), "analysis_runs", [column], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM analysis_runs")).scalar_one()
    if count:
        raise RuntimeError(f"Refusing to drop analysis_runs: {count} run(s) hold computed results. Export them first.")
    for column in ("dataset_id", "analysis_plan_id", "project_id"):
        op.drop_index(op.f(f"ix_analysis_runs_{column}"), table_name="analysis_runs")
    op.drop_table("analysis_runs")
