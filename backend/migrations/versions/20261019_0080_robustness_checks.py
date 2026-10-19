"""Add robustness_checks: one fingerprint comparison against a stored analysis_run each (M5.4.2, spec 5.9).

Revision ID: 20261019_0080
Revises: 20261019_0079
Create Date: 2026-10-19

Nothing is backfilled. The downgrade refuses while any check exists, because a check is the record of a
robustness comparison, same convention as analysis_runs (migration 20261017_0074). Chained after
20261019_0079 (qual_codes), which claimed 0079 first in this working tree.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261019_0080"
down_revision: Union[str, Sequence[str], None] = "20261019_0079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "robustness_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("base_run_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("compare_dataset_id", sa.Uuid(), nullable=True),
        sa.Column("compare_plan_id", sa.Uuid(), nullable=True),
        sa.Column("compare_run_id", sa.Uuid(), nullable=True),
        sa.Column("base_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("compare_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("identical", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["base_run_id"], ["analysis_runs.id"]),
        sa.ForeignKeyConstraint(["compare_dataset_id"], ["dataset_versions.id"]),
        sa.ForeignKeyConstraint(["compare_plan_id"], ["analysis_plans.id"]),
        sa.ForeignKeyConstraint(["compare_run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("project_id", "base_run_id"):
        op.create_index(op.f(f"ix_robustness_checks_{column}"), "robustness_checks", [column], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM robustness_checks")).scalar_one()
    if count:
        raise RuntimeError(f"Refusing to drop robustness_checks: {count} check(s) hold computed comparisons. Export them first.")
    for column in ("base_run_id", "project_id"):
        op.drop_index(op.f(f"ix_robustness_checks_{column}"), table_name="robustness_checks")
    op.drop_table("robustness_checks")
