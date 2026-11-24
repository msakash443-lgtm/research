"""Documented discussion of screening conflicts (X.225).

Additive: one new table, `screening_discussion_notes`. Downgrading refuses to drop a table that has rows.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261112_0104"
down_revision: Union[str, Sequence[str], None] = "20261110_0103"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "screening_discussion_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("decision_seq", sa.Integer(), nullable=False),
        sa.Column("author", sa.String(length=100), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_screening_discussion_notes_project_id", "screening_discussion_notes", ["project_id"])
    op.create_index("ix_screening_discussion_notes_source_id", "screening_discussion_notes", ["source_id"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM screening_discussion_notes)")).scalar():
        raise RuntimeError("screening_discussion_notes still has rows; refusing to drop it")
    op.drop_index("ix_screening_discussion_notes_source_id", table_name="screening_discussion_notes")
    op.drop_index("ix_screening_discussion_notes_project_id", table_name="screening_discussion_notes")
    op.drop_table("screening_discussion_notes")
