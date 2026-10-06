"""Add projects.discipline and projects.config_json (discipline profile and its validated overrides).

Revision ID: 20261003_0018
Revises: 20261003_0017
Create Date: 2026-10-03

Both nullable: existing projects have no profile and behave exactly as before.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_0018"
down_revision: Union[str, Sequence[str], None] = "20261003_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("discipline", sa.String(length=60), nullable=True))
    op.add_column("projects", sa.Column("config_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "config_json")
    op.drop_column("projects", "discipline")
