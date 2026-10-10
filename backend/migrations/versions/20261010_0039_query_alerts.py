"""Add search_alerts: alert subscriptions on saved searches (plan M1.12, spec 5.2.4).

One row per saved search; the worker sweeps `next_run_at` and enqueues a G2-gated `query_alert`
task per due slot. `last_run_at`/`last_run_version`/`last_new` describe the last completed run.

Revision ID: 20261010_0039
Revises: 20261009_0038
Create Date: 2026-10-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261010_0039"
down_revision: Union[str, Sequence[str], None] = "20261009_0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_alerts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("search_id", sa.Uuid(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="86400"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_version", sa.Integer(), nullable=True),
        sa.Column("last_new", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("interval_seconds >= 3600", name="ck_search_alert_interval_min"),
        sa.CheckConstraint("interval_seconds <= 2592000", name="ck_search_alert_interval_max"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("search_id", name="uq_search_alert_search"),
    )
    op.create_index("ix_search_alerts_project_id", "search_alerts", ["project_id"])
    op.create_index("ix_search_alerts_search_id", "search_alerts", ["search_id"])


def downgrade() -> None:
    op.drop_index("ix_search_alerts_search_id", table_name="search_alerts")
    op.drop_index("ix_search_alerts_project_id", table_name="search_alerts")
    op.drop_table("search_alerts")
