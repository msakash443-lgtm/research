"""Add projects.research_type and projects.stage_plan_json (X.43.2, Q22).

Revision ID: 20261015_0066
Revises: 20261015_0065
Create Date: 2026-10-15

`research_type` names a type in `app/research_types/`; `stage_plan_json` is a snapshot of that type taken
when it was set, so editing a type file later never changes an existing project's stages. Nothing reads
either column yet (X.43.3).

Every existing project is backfilled to `full_journey` version 1, today's exact 19-stage chain, so nothing
about it changes. The snapshot is written out below rather than read from the type file, so this migration
does the same thing however that file changes later. It is the column's server default: SQLite can only
add a NOT NULL column with a default, and the default fills existing rows in the same statement, with no
rebuild of `projects`. The application always writes the column itself (`Project` ORM default).

The downgrade refuses while any project is on another research type: dropping the columns would silently
put that project back on the full journey.
"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261015_0066"
down_revision: Union[str, Sequence[str], None] = "20261015_0065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FULL_JOURNEY_STAGES = (
    ("idea", None),
    ("scoped", "G1"),
    ("search_planned", "G2"),
    ("retrieved", None),
    ("screened", "G3"),
    ("extracted", "G4"),
    ("synthesized", None),
    ("gaps_selected", "G5"),
    ("framework", "G6"),
    ("design_approved", "G7"),
    ("data_collected", None),
    ("plan_locked", "G8"),
    ("analyzed", "G9"),
    ("drafted", "G10"),
    ("revised", None),
    ("submission_ready", "G11"),
    ("submitted", None),
    ("revision_loop", None),
    ("accepted", None),
)

FULL_JOURNEY_V1 = {
    "name": "full_journey",
    "version": 1,
    "label": "Full research journey",
    "description": "Every stage from idea to acceptance, including primary data collection and a locked analysis plan.",
    "human_participants": True,
    "stages": [{"stage": stage, "label": None, "gate": gate} for stage, gate in _FULL_JOURNEY_STAGES],
}


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("research_type", sa.String(60), nullable=False, server_default="full_journey")
    )
    op.add_column(
        "projects", sa.Column("stage_plan_json", sa.JSON(), nullable=False, server_default=json.dumps(FULL_JOURNEY_V1))
    )


def downgrade() -> None:
    if not op.get_context().as_sql:
        others = op.get_bind().execute(
            sa.text("SELECT COUNT(*) FROM projects WHERE research_type != 'full_journey'")
        ).scalar()
        if others:
            raise RuntimeError(
                f"{others} project(s) use a research type other than full_journey; refusing to drop "
                "projects.research_type and projects.stage_plan_json, which would put them back on the full journey."
            )
    op.drop_column("projects", "stage_plan_json")
    op.drop_column("projects", "research_type")
