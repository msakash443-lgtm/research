"""Store per-field confidence and make AI extraction task retries idempotent.

Revision ID: 20261012_0041
Revises: 20261011_0040
Create Date: 2026-10-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261012_0041"
down_revision: Union[str, Sequence[str], None] = "20261011_0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("extractions") as batch:
        batch.add_column(sa.Column("confidence_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("task_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_extractions_task_id_tasks", "tasks", ["task_id"], ["id"])
        batch.create_unique_constraint("uq_extraction_task", ["task_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM extractions WHERE task_id IS NOT NULL")):
        raise RuntimeError("Cannot downgrade M3.3 provenance while AI extraction rows reference task ids")
    with op.batch_alter_table("extractions") as batch:
        batch.drop_constraint("uq_extraction_task", type_="unique")
        batch.drop_constraint("fk_extractions_task_id_tasks", type_="foreignkey")
        batch.drop_column("task_id")
        batch.drop_column("confidence_json")
