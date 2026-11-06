"""Add persistent journal candidates and official-list provenance (M7.4)."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261022_0094"
down_revision: Union[str, Sequence[str], None] = "20261022_0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "journal_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("scope_terms", sa.JSON(), nullable=False),
        sa.Column("indexes", sa.JSON(), nullable=False),
        sa.Column("apc", sa.Float(), nullable=True),
        sa.Column("review_days", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.String(length=2000), nullable=False),
        sa.Column("checked_at", sa.String(length=64), nullable=False),
        sa.Column("official_predatory", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_journal_candidates_project_id", "journal_candidates", ["project_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM journal_candidates")).scalar_one():
        raise RuntimeError("Refusing to drop journal_candidates while rows exist")
    op.drop_index("ix_journal_candidates_project_id", table_name="journal_candidates")
    op.drop_table("journal_candidates")
