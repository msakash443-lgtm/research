"""Add submission reminders (M7.5)."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261022_0096"
down_revision: Union[str, Sequence[str], None] = "20261022_0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "submission_reminders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_submission_reminders_project_id", "submission_reminders", ["project_id"])
    op.create_index("ix_submission_reminders_submission_id", "submission_reminders", ["submission_id"])
    op.create_index("ix_submission_reminders_due_at", "submission_reminders", ["due_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM submission_reminders")).scalar_one():
        raise RuntimeError("Refusing to drop submission_reminders while rows exist")
    op.drop_index("ix_submission_reminders_due_at", table_name="submission_reminders")
    op.drop_index("ix_submission_reminders_submission_id", table_name="submission_reminders")
    op.drop_index("ix_submission_reminders_project_id", table_name="submission_reminders")
    op.drop_table("submission_reminders")
