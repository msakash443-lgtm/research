"""Hold a token reservation for each model call in flight (X.110).

Revision ID: 20261022_0089
Revises: 20261022_0088
Create Date: 2026-10-22

Additive: one nullable column. `reserved_tokens` is set while a call is in flight and cleared when its
usage is recorded, so concurrent calls see each other's budget. Downgrade refuses while a call is in flight.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261022_0089"
down_revision: Union[str, Sequence[str], None] = "20261022_0088"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("llm_usage", sa.Column("reserved_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    in_flight = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM llm_usage WHERE reserved_tokens IS NOT NULL")).scalar()
    if in_flight:
        raise RuntimeError(f"Refusing to drop llm_usage.reserved_tokens: {in_flight} model call(s) are still in flight.")
    op.drop_column("llm_usage", "reserved_tokens")
