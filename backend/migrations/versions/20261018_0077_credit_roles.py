"""Add project_members.credit_roles (M6.6).

Revision ID: 20261018_0077
Revises: 20261018_0076
Create Date: 2026-10-18

Not-null JSON array, default `[]`: the CRediT contribution roles a project member holds for the manuscript's
"Author contributions" statement. Existing members backfill to an empty list (no roles assigned yet).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261018_0077"
down_revision: Union[str, Sequence[str], None] = "20261018_0076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("project_members", sa.Column("credit_roles", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("project_members", "credit_roles")
