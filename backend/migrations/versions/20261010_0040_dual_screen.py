"""Add projects.dual_screen_enabled (M2.4.1, spec 5.3.3: two independent reviewers, blinded).

Revision ID: 20261010_0040
Revises: 20261010_0039
Create Date: 2026-10-10

Off by default (rule 18): existing projects keep today's single-shared-decision screening exactly as
before. Turning it on changes how new screening decisions at both stages are read (needs two distinct
human deciders to agree before a source counts as decided) but never rewrites existing rows.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261010_0040"
down_revision: Union[str, Sequence[str], None] = "20261010_0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("dual_screen_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("projects", "dual_screen_enabled")
