"""Add `journal_candidates.issns` (X.201): ISSNs the owner supplied, matched against a source's ISSN."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261119_0107"
down_revision: Union[str, Sequence[str], None] = "20261119_0106"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("journal_candidates", sa.Column("issns", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("journal_candidates", "issns")
