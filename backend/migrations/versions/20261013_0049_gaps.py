"""Add scholar-curated evidence-backed gaps (M3.9)."""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261013_0049"
down_revision: Union[str, Sequence[str], None] = "20261013_0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gaps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("supporting_paper_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("matrix_cell_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("future_research", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "statement", name="uq_gap_project_statement"),
    )
    op.create_index("ix_gaps_project_id", "gaps", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_gaps_project_id", table_name="gaps")
    op.drop_table("gaps")
