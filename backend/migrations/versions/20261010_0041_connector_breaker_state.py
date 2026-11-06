"""Add connector_breaker_state (M1.3.8): cross-process circuit breaker state per connector.

Revision ID: 20261010_0041
Revises: 20261010_0040
Create Date: 2026-10-10

Off by default (rule 18): `CONNECTOR_BREAKER_SHARED=false` keeps today's per-instance,
in-memory-only breaker (M1.3.2); the table exists but nothing writes to it until the setting
is turned on. New table, no backfill needed.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261010_0041"
down_revision: Union[str, Sequence[str], None] = "20261010_0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "connector_breaker_state",
        sa.Column("name", sa.String(length=100), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("open_until", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("connector_breaker_state")
