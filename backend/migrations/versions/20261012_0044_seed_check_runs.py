"""Seed-based false-exclusion check runs (M2.9.1, spec 11 / 7.7).

Revision ID: 20261012_0044
Revises: 20261010_0043
Create Date: 2026-10-12

Additive: one new table, `seed_check_runs`, holding what the AI pre-screen answered for each seed paper
that matches a stored source, plus the model, prompt version and settings used. Nothing existing changes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261012_0044"
down_revision: Union[str, Sequence[str], None] = "20261010_0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "seed_check_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("min_confidence", sa.Float(), nullable=False),
        sa.Column("autoexclude_threshold", sa.Float(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("entries", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_seed_check_runs_project_id", "seed_check_runs", ["project_id"])
    op.create_index("ix_seed_check_runs_created_at", "seed_check_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_seed_check_runs_created_at", table_name="seed_check_runs")
    op.drop_index("ix_seed_check_runs_project_id", table_name="seed_check_runs")
    op.drop_table("seed_check_runs")
