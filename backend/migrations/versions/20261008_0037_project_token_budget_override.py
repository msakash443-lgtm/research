"""Add projects.token_budget_override (per-project override of the global token budget, M0.9.3).

Revision ID: 20261008_0037
Revises: 20261008_0036
Create Date: 2026-10-08

Nullable: existing projects have no override and keep using the global `PROJECT_TOKEN_BUDGET` setting,
exactly as before. Non-negative is validated at the API boundary (`ProjectBudgetUpdate`), the same way the
global setting itself is validated (no DB check), not with a DB CHECK constraint.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261008_0037"
down_revision: Union[str, Sequence[str], None] = "20261008_0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("token_budget_override", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "token_budget_override")
