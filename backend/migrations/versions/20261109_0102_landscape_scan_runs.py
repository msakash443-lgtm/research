"""Landscape scan runs (spec 5.1.2, X.189).

Revision ID: 20261109_0102
Revises: 20261029_0101
Create Date: 2026-11-09

Additive: one new table, `landscape_scan_runs`, holding a stored multi-connector landscape scan (query,
per-service counts/failures, deduplicated record ids). Nothing existing changes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261109_0102"
down_revision: Union[str, Sequence[str], None] = "20261029_0101"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "landscape_scan_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("reviews_since", sa.Integer(), nullable=False),
        sa.Column("services", sa.JSON(), nullable=False),
        sa.Column("record_ids", sa.JSON(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_landscape_scan_runs_project_id", "landscape_scan_runs", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_landscape_scan_runs_project_id", table_name="landscape_scan_runs")
    op.drop_table("landscape_scan_runs")
