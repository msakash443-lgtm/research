"""Add the gates table and give every existing project its eleven pending gates.

Revision ID: 20261003_0009
Revises: 20261003_0008
Create Date: 2026-10-03
"""

import uuid
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261003_0009"
down_revision: Union[str, Sequence[str], None] = "20261003_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CODES = tuple(f"G{n}" for n in range(1, 12))
STATUSES = ("pending", "approved", "rejected")


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*CODES, name="gate_code").create(bind, checkfirst=True)
    sa.Enum(*STATUSES, name="gate_status").create(bind, checkfirst=True)
    # The types now exist; stop create_table from issuing CREATE TYPE again on PostgreSQL.
    code = sa.Enum(*CODES, name="gate_code").with_variant(
        postgresql.ENUM(*CODES, name="gate_code", create_type=False), "postgresql"
    )
    status = sa.Enum(*STATUSES, name="gate_status").with_variant(
        postgresql.ENUM(*STATUSES, name="gate_status", create_type=False), "postgresql"
    )
    gates = op.create_table(
        "gates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("code", code, nullable=False),
        sa.Column("status", status, nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.String(length=100), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "code", name="uq_project_gate"),
    )
    op.create_index("ix_gates_project_id", "gates", ["project_id"])

    if context.is_offline_mode():
        # No rows to read when generating SQL; the gate endpoints create missing gates on demand.
        return
    projects_table = sa.table("projects", sa.column("id", sa.Uuid()))
    project_ids = [row.id for row in bind.execute(sa.select(projects_table.c.id)).fetchall()]
    if project_ids:
        op.bulk_insert(
            gates,
            [
                {"id": uuid.uuid4(), "project_id": pid, "code": c, "status": "pending"}
                for pid in project_ids
                for c in CODES
            ],
        )


def downgrade() -> None:
    op.drop_index("ix_gates_project_id", table_name="gates")
    op.drop_table("gates")
    bind = op.get_bind()
    sa.Enum(name="gate_status").drop(bind, checkfirst=True)
    sa.Enum(name="gate_code").drop(bind, checkfirst=True)
