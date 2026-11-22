"""Add `project_responsibilities` (X.226): the responsibility matrix, separate from roles and CRediT."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261119_0108"
down_revision: Union[str, Sequence[str], None] = "20261119_0107"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_responsibilities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("area", sa.String(length=30), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("assigned_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "area", "user_id", name="uq_project_responsibility"),
    )
    op.create_index("ix_project_responsibilities_project_id", "project_responsibilities", ["project_id"])
    op.create_index("ix_project_responsibilities_user_id", "project_responsibilities", ["user_id"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM project_responsibilities)")).scalar():
        raise RuntimeError("project_responsibilities still has rows; refusing to drop it")
    op.drop_index("ix_project_responsibilities_user_id", table_name="project_responsibilities")
    op.drop_index("ix_project_responsibilities_project_id", table_name="project_responsibilities")
    op.drop_table("project_responsibilities")
