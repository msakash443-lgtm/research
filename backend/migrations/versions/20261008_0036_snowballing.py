"""Snowballing (M1.9): `snowball_runs` log table and `sources.found_via` provenance.

Revision ID: 20261008_0036
Revises: 20261008_0035
Create Date: 2026-10-08

Additive: a new table and a nullable JSON column (plain ADD/DROP COLUMN, so SQLite keeps the
triggers on other tables). Existing sources keep `found_via` NULL.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261008_0036"
down_revision: Union[str, Sequence[str], None] = "20261008_0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "snowball_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("connector", sa.String(length=50), nullable=False),
        sa.Column("directions", sa.JSON(), nullable=False),
        sa.Column("rounds", sa.Integer(), nullable=False),
        sa.Column("caps", sa.JSON(), nullable=False),
        sa.Column("starts", sa.JSON(), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("rounds >= 1", name="ck_snowball_run_rounds"),
    )
    op.create_index("ix_snowball_runs_project_id", "snowball_runs", ["project_id"])
    op.add_column("sources", sa.Column("found_via", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "found_via")
    op.drop_index("ix_snowball_runs_project_id", table_name="snowball_runs")
    op.drop_table("snowball_runs")
