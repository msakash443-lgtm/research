"""Screening decisions: the append-only screening_decisions table.

Revision ID: 20261005_0027
Revises: 20261005_0026
Create Date: 2026-10-05

Additive. Rows are never updated or deleted by the application (an ORM guard enforces it); there is no
database trigger yet, so a raw-SQL writer could still change them.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261005_0027"
down_revision: Union[str, Sequence[str], None] = "20261005_0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "screening_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=10), nullable=False),
        sa.Column("reason_code", sa.String(length=10), nullable=True),
        sa.Column("decided_by", sa.String(length=10), nullable=False),
        sa.Column("decider", sa.String(length=100), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "stage", "seq", name="uq_screening_seq"),
    )
    op.create_index("ix_screening_decisions_project_id", "screening_decisions", ["project_id"])
    op.create_index("ix_screening_decisions_source_id", "screening_decisions", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_screening_decisions_source_id", table_name="screening_decisions")
    op.drop_index("ix_screening_decisions_project_id", table_name="screening_decisions")
    op.drop_table("screening_decisions")
