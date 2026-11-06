"""Add projects.ai_involvement and projects.ai_model_route (X.31.7, Q14/Q27).

Revision ID: 20261012_0046
Revises: 20261012_0045
Create Date: 2026-10-12

Additive. Every existing project gets `suggestions_only` (the owner's Q14 default) and the `standard` route,
so what the AI steps already do is unchanged until an owner turns AI off or picks the local model.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261012_0046"
down_revision: Union[str, Sequence[str], None] = "20261012_0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    involvement = sa.Enum("off", "suggestions_only", "full", name="ai_involvement")
    route = sa.Enum("standard", "local", name="ai_model_route")
    # add_column does not create the PostgreSQL enum types
    involvement.create(op.get_bind(), checkfirst=True)
    route.create(op.get_bind(), checkfirst=True)
    op.add_column("projects", sa.Column("ai_involvement", involvement, nullable=False, server_default="suggestions_only"))
    op.add_column("projects", sa.Column("ai_model_route", route, nullable=False, server_default="standard"))


def downgrade() -> None:
    op.drop_column("projects", "ai_model_route")
    op.drop_column("projects", "ai_involvement")
    sa.Enum(name="ai_model_route").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="ai_involvement").drop(op.get_bind(), checkfirst=True)
