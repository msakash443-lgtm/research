"""Add qual_codes: the persisted qualitative codebook (M5.5.2). Backs app/qual_coding.py's accept/rename/merge
state; app/qual_codebook_store.py is the only writer.

Revision ID: 20261019_0079
Revises: 20261019_0078
Create Date: 2026-10-19

Nothing is backfilled: no accepted suggestion before this migration has a codebook row (the gap this task
closes). The downgrade refuses while any code exists, because a merge's dropped definition lives only in the
audit log, not a second table, so dropping the row would be the only remaining copy of its name.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261019_0079"
down_revision: Union[str, Sequence[str], None] = "20261019_0078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qual_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("definition", sa.Text(), nullable=True),
        sa.Column("redirect_to", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_qual_code_project_name"),
    )
    op.create_index(op.f("ix_qual_codes_project_id"), "qual_codes", ["project_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM qual_codes")).scalar_one()
    if count:
        raise RuntimeError(f"Refusing to drop qual_codes: {count} row(s) hold codebook entries. Export them first.")
    op.drop_index(op.f("ix_qual_codes_project_id"), table_name="qual_codes")
    op.drop_table("qual_codes")
