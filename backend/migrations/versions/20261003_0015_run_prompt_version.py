"""Add research_runs.prompt_version (which prompt file/version produced the answer).

Revision ID: 20261003_0015
Revises: 20261003_0014
Create Date: 2026-10-03

Runs that finished before this migration keep NULL: their prompt was the inline text that
became `evidence_synthesis@1`, but nothing recorded it at the time, so it is not guessed.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_0015"
down_revision: Union[str, Sequence[str], None] = "20261003_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("research_runs", sa.Column("prompt_version", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("research_runs", "prompt_version")
