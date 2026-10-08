"""Research runs: confidence and abstention (M0.8.6).

Revision ID: 20261007_0030
Revises: 20261007_0029
Create Date: 2026-10-07

Additive and nullable. Runs made before this keep NULL (the model gave no structured signal; not guessed).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261007_0030"
down_revision: Union[str, Sequence[str], None] = "20261007_0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("research_runs", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("research_runs", sa.Column("insufficient_evidence", sa.Boolean(), nullable=True))
    op.add_column("research_runs", sa.Column("insufficient_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    # Plain DROP COLUMN (not a batch rebuild) so SQLite keeps the audit/excerpt triggers.
    op.drop_column("research_runs", "insufficient_reason")
    op.drop_column("research_runs", "insufficient_evidence")
    op.drop_column("research_runs", "confidence")
