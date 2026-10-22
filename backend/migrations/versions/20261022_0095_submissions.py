"""Add submission tracking (M7.5)."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261022_0095"
down_revision: Union[str, Sequence[str], None] = "20261022_0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("journal_name", sa.String(length=300), nullable=False),
        sa.Column("manuscript_url", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_submissions_project_id", "submissions", ["project_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM submissions")).scalar_one():
        raise RuntimeError("Refusing to drop submissions while rows exist")
    op.drop_index("ix_submissions_project_id", table_name="submissions")
    op.drop_table("submissions")
