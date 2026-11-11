"""Add conceptual model paths (M4.2).

Revision ID: 20261015_0065
Revises: 20261015_0064
Create Date: 2026-10-15

Additive: one new table. Composite FKs keep every construct in the path's project, as
`theory_constructs` does (M3.7).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261015_0065"
down_revision: Union[str, Sequence[str], None] = "20261015_0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conceptual_paths",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("from_construct_id", sa.Uuid(), nullable=False), sa.Column("to_construct_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False), sa.Column("via_construct_id", sa.Uuid(), nullable=True),
        sa.Column("source_ids", sa.JSON(), nullable=False), sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["from_construct_id", "project_id"], ["constructs.id", "constructs.project_id"]),
        sa.ForeignKeyConstraint(["to_construct_id", "project_id"], ["constructs.id", "constructs.project_id"]),
        sa.ForeignKeyConstraint(["via_construct_id", "project_id"], ["constructs.id", "constructs.project_id"]),
        sa.CheckConstraint("kind IN ('direct', 'mediation', 'moderation')", name="ck_conceptual_path_kind"),
        sa.CheckConstraint("(kind = 'direct') = (via_construct_id IS NULL)", name="ck_conceptual_path_via"),
        sa.CheckConstraint("from_construct_id <> to_construct_id", name="ck_conceptual_path_not_self"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "from_construct_id", "to_construct_id", "kind", "via_construct_id", name="uq_conceptual_path"),
    )
    for column in ("project_id", "from_construct_id", "to_construct_id", "via_construct_id"):
        op.create_index(f"ix_conceptual_paths_{column}", "conceptual_paths", [column])


def downgrade() -> None:
    # Refuse to drop paths people drew: they are researcher-authored, not regenerable.
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM conceptual_paths")).scalar():
        raise RuntimeError("conceptual_paths still has rows; refusing to drop researcher-drawn conceptual paths")
    for column in ("via_construct_id", "to_construct_id", "from_construct_id", "project_id"):
        op.drop_index(f"ix_conceptual_paths_{column}", table_name="conceptual_paths")
    op.drop_table("conceptual_paths")
