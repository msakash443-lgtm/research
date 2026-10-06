"""Add project_members (roles) and backfill each project's owner.

Revision ID: 20261002_0004
Revises: 20261002_0003
Create Date: 2026-10-02
"""

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261002_0004"
down_revision: Union[str, Sequence[str], None] = "20261002_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    role = sa.Enum("owner", "co_author", "supervisor", "reviewer", name="project_role")
    members = op.create_table(
        "project_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])

    bind = op.get_bind()
    projects_table = sa.table("projects", sa.column("id", sa.Uuid()), sa.column("owner_id", sa.Uuid()))
    projects = bind.execute(sa.select(projects_table.c.id, projects_table.c.owner_id)).fetchall()
    if projects:
        op.bulk_insert(
            members,
            [{"id": uuid.uuid4(), "project_id": p.id, "user_id": p.owner_id, "role": "owner"} for p in projects],
        )


def downgrade() -> None:
    op.drop_index("ix_project_members_user_id", table_name="project_members")
    op.drop_index("ix_project_members_project_id", table_name="project_members")
    op.drop_table("project_members")
    sa.Enum(name="project_role").drop(op.get_bind(), checkfirst=True)
