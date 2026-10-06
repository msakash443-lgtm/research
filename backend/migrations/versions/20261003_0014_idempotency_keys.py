"""Idempotency keys: tasks.idempotency_key and sources.ingest_key, each unique per project.

Revision ID: 20261003_0014
Revises: 20261003_0013
Create Date: 2026-10-03

Unique *indexes* (not constraints) so SQLite needs no table rebuild. NULLs never collide, so
tasks without a key and sources people add by hand are unaffected.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_0014"
down_revision: Union[str, Sequence[str], None] = "20261003_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("idempotency_key", sa.String(length=200), nullable=True))
    op.create_index("uq_task_idempotency", "tasks", ["project_id", "idempotency_key"], unique=True)
    op.add_column("sources", sa.Column("ingest_key", sa.String(length=100), nullable=True))
    op.create_index("uq_source_ingest_key", "sources", ["project_id", "ingest_key"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_source_ingest_key", table_name="sources")
    op.drop_column("sources", "ingest_key")
    op.drop_index("uq_task_idempotency", table_name="tasks")
    op.drop_column("tasks", "idempotency_key")
