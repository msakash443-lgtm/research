"""Add deviation_logs: one immutable deviation log per (locked plan, actual run) comparison (M5.8.4, spec 5.9).

Revision ID: 20261020_0087
Revises: 20261020_0086
Create Date: 2026-10-22

Nothing is backfilled. The downgrade refuses while any log exists, because a log is the researcher's recorded
reasons for deviating from a pre-registered plan; same convention as robustness_checks (migration 20261019_0080).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261020_0087"
down_revision: Union[str, Sequence[str], None] = "20261020_0086"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deviation_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("planned_plan_id", sa.Uuid(), nullable=False),
        sa.Column("actual_run_id", sa.Uuid(), nullable=False),
        sa.Column("planned_plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("actual_plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("planned_dataset_sha256", sa.String(length=64), nullable=False),
        sa.Column("actual_dataset_sha256", sa.String(length=64), nullable=False),
        sa.Column("log_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["planned_plan_id"], ["analysis_plans.id"]),
        sa.ForeignKeyConstraint(["actual_run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("project_id", "planned_plan_id"):
        op.create_index(op.f(f"ix_deviation_logs_{column}"), "deviation_logs", [column], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM deviation_logs")).scalar_one()
    if count:
        raise RuntimeError(f"Refusing to drop deviation_logs: {count} log(s) hold recorded reasons. Export them first.")
    for column in ("planned_plan_id", "project_id"):
        op.drop_index(op.f(f"ix_deviation_logs_{column}"), table_name="deviation_logs")
    op.drop_table("deviation_logs")
