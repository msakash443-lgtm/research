"""Add `projects.peer_reviewed_only` (X.196): preprint-flagged sources not citable when on."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261119_0106"
down_revision: Union[str, Sequence[str], None] = "20261119_0105"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("peer_reviewed_only", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("projects", "peer_reviewed_only")
