"""Add the append-only audit_events table.

Revision ID: 20261002_0005
Revises: 20261002_0004
Create Date: 2026-10-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261002_0005"
down_revision: Union[str, Sequence[str], None] = "20261002_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_project_id", "audit_events", ["project_id"])
    op.create_index("ix_audit_events_actor", "audit_events", ["actor"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_timestamp", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_index("ix_audit_events_actor", table_name="audit_events")
    op.drop_index("ix_audit_events_project_id", table_name="audit_events")
    op.drop_table("audit_events")
