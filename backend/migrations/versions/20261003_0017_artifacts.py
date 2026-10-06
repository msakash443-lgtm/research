"""Artifacts and their dependency edges (re-entry marks later work stale), and register existing completed runs.

Revision ID: 20261003_0017
Revises: 20261003_0016
Create Date: 2026-10-03

Existing completed research runs become `current` artifacts at the `synthesized` stage (the spec's stage for
evidence synthesis), so a later re-entry can mark them stale. Downgrade drops both tables.
"""

import uuid
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261003_0017"
down_revision: Union[str, Sequence[str], None] = "20261003_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STAGES = (
    "idea", "scoped", "search_planned", "retrieved", "screened", "extracted", "synthesized",
    "gaps_selected", "framework", "design_approved", "data_collected", "plan_locked", "analyzed",
    "drafted", "revised", "submission_ready", "submitted", "revision_loop", "accepted",
)
STATUSES = ("current", "stale")


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*STATUSES, name="artifact_status").create(bind, checkfirst=True)
    status_type = sa.Enum(*STATUSES, name="artifact_status").with_variant(
        postgresql.ENUM(*STATUSES, name="artifact_status", create_type=False), "postgresql"
    )
    # `project_stage` already exists (created with projects.stage); reuse it.
    stage_type = sa.Enum(*STAGES, name="project_stage").with_variant(
        postgresql.ENUM(*STAGES, name="project_stage", create_type=False), "postgresql"
    )
    artifacts = op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("ref_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("stage", stage_type, nullable=False),
        sa.Column("status", status_type, nullable=False, server_default="current"),
        sa.Column("stale_reason", sa.Text(), nullable=True),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "kind", "ref_id", name="uq_artifact_ref"),
    )
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])
    op.create_table(
        "artifact_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("upstream_id", sa.Uuid(), nullable=False),
        sa.Column("downstream_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["upstream_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["downstream_id"], ["artifacts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("upstream_id", "downstream_id", name="uq_artifact_edge"),
        sa.CheckConstraint("upstream_id != downstream_id", name="ck_artifact_edge_not_self"),
    )
    op.create_index("ix_artifact_edges_project_id", "artifact_edges", ["project_id"])
    op.create_index("ix_artifact_edges_upstream_id", "artifact_edges", ["upstream_id"])
    op.create_index("ix_artifact_edges_downstream_id", "artifact_edges", ["downstream_id"])

    if context.is_offline_mode():
        return  # data-only step; nothing to read when generating SQL
    runs = sa.table("research_runs", sa.column("id", sa.Uuid()), sa.column("project_id", sa.Uuid()))
    completed = bind.execute(sa.select(runs.c.id, runs.c.project_id).where(sa.text("status = 'completed'"))).fetchall()
    if completed:
        op.bulk_insert(
            artifacts,
            [
                {
                    "id": uuid.uuid4(), "project_id": r.project_id, "kind": "research_run", "ref_id": str(r.id),
                    "stage": "synthesized", "status": "current", "created_by": "agent:research-run",
                }
                for r in completed
            ],
        )


def downgrade() -> None:
    op.drop_index("ix_artifact_edges_downstream_id", table_name="artifact_edges")
    op.drop_index("ix_artifact_edges_upstream_id", table_name="artifact_edges")
    op.drop_index("ix_artifact_edges_project_id", table_name="artifact_edges")
    op.drop_table("artifact_edges")
    op.drop_index("ix_artifacts_project_id", table_name="artifacts")
    op.drop_table("artifacts")
    sa.Enum(name="artifact_status").drop(op.get_bind(), checkfirst=True)
