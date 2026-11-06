"""Link highlight notes to sources and immutable source excerpts (X.31.3)."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261017_0068"
down_revision: Union[str, Sequence[str], None] = "20261017_0067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode recreates the table only on SQLite, where ALTER TABLE cannot add a foreign-key
    # constraint. On PostgreSQL it emits plain ALTERs: recreating `notes` would drop notes_pkey,
    # which note_revisions, note_attachments and research_context_items reference.
    with op.batch_alter_table("notes", recreate="auto") as batch:
        batch.add_column(sa.Column("source_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("excerpt_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_notes_source_id_sources", "sources", ["source_id"], ["id"])
        batch.create_foreign_key("fk_notes_excerpt_id_source_excerpts", "source_excerpts", ["excerpt_id"], ["id"])
    op.create_index("ix_notes_source_id", "notes", ["source_id"])
    op.create_index("ix_notes_excerpt_id", "notes", ["excerpt_id"])


def downgrade() -> None:
    op.drop_index("ix_notes_excerpt_id", table_name="notes")
    op.drop_index("ix_notes_source_id", table_name="notes")
    with op.batch_alter_table("notes", recreate="auto") as batch:
        batch.drop_constraint("fk_notes_excerpt_id_source_excerpts", type_="foreignkey")
        batch.drop_constraint("fk_notes_source_id_sources", type_="foreignkey")
        batch.drop_column("excerpt_id")
        batch.drop_column("source_id")
