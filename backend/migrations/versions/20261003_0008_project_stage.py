"""Add projects.stage (workflow state machine, spec Appendix C).

Revision ID: 20261003_0008
Revises: 20261003_0007
Create Date: 2026-10-03

Existing projects start at `idea`. `projects.status` (active/archived lifecycle) is kept.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_0008"
down_revision: Union[str, Sequence[str], None] = "20261003_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STAGES = (
    "idea", "scoped", "search_planned", "retrieved", "screened", "extracted", "synthesized",
    "gaps_selected", "framework", "design_approved", "data_collected", "plan_locked", "analyzed",
    "drafted", "revised", "submission_ready", "submitted", "revision_loop", "accepted",
)


def upgrade() -> None:
    stage = sa.Enum(*STAGES, name="project_stage")
    stage.create(op.get_bind(), checkfirst=True)  # add_column does not create the PostgreSQL enum type
    op.add_column("projects", sa.Column("stage", stage, nullable=False, server_default="idea"))


def downgrade() -> None:
    op.drop_column("projects", "stage")
    sa.Enum(name="project_stage").drop(op.get_bind(), checkfirst=True)
