"""Add dataset_versions: ingested datasets and their cleaned versions (M5.1, spec 5.8.3).

Revision ID: 20261017_0071
Revises: 20261017_0070
Create Date: 2026-10-16

Nothing is backfilled. The downgrade refuses while any version exists, because dropping the table would discard
participant data that the scholar uploaded.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261017_0071"
down_revision: Union[str, Sequence[str], None] = "20261017_0070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("column_names", sa.JSON(), nullable=False),
        sa.Column("cleaning_steps", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["dataset_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "version_no", name="uq_dataset_version_project_no"),
    )
    op.create_index(op.f("ix_dataset_versions_project_id"), "dataset_versions", ["project_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM dataset_versions")).scalar_one()
    if count:
        raise RuntimeError(f"Refusing to drop dataset_versions: {count} version(s) hold uploaded data. Export them first.")
    op.drop_index(op.f("ix_dataset_versions_project_id"), table_name="dataset_versions")
    op.drop_table("dataset_versions")
