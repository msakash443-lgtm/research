"""Store immutable generated manuscript front matter outputs (M6.10.2).

Revision ID: 20261022_0091
Revises: 20261022_0090
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261022_0091"
down_revision: Union[str, Sequence[str], None] = "20261022_0090"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "manuscript_outputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["source_version_id"], ["document_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "source_version_id", "kind", "created_at", name="uq_manuscript_output_event"),
    )
    op.create_index("ix_manuscript_outputs_project_id", "manuscript_outputs", ["project_id"])
    op.create_index("ix_manuscript_outputs_document_id", "manuscript_outputs", ["document_id"])
    op.create_index("ix_manuscript_outputs_source_version_id", "manuscript_outputs", ["source_version_id"])
    op.create_index("ix_manuscript_outputs_kind", "manuscript_outputs", ["kind"])



def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        count = bind.execute(sa.text("SELECT COUNT(*) FROM manuscript_outputs")).scalar_one()
    else:
        count = bind.execute(sa.text("SELECT COUNT(*) FROM manuscript_outputs")).scalar_one()
    if count:
        raise RuntimeError("Refusing to drop manuscript_outputs while generated outputs exist")
    op.drop_index("ix_manuscript_outputs_kind", table_name="manuscript_outputs")
    op.drop_index("ix_manuscript_outputs_source_version_id", table_name="manuscript_outputs")
    op.drop_index("ix_manuscript_outputs_document_id", table_name="manuscript_outputs")
    op.drop_index("ix_manuscript_outputs_project_id", table_name="manuscript_outputs")
    op.drop_table("manuscript_outputs")
