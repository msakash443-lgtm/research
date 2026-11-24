"""Provenance for explicit Research Assistant captures."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261123_0105"
down_revision: Union[str, Sequence[str], None] = "20261123_0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("notes", "research_context_items"):
        op.add_column(table, sa.Column("captured_stage", sa.String(80), nullable=True))
        op.add_column(table, sa.Column("originating_conversation_id", sa.Uuid(), nullable=True))
        op.add_column(table, sa.Column("originating_message_id", sa.Uuid(), nullable=True))
        op.add_column(table, sa.Column("text_origin", sa.String(20), nullable=True))
    op.create_foreign_key("fk_notes_assistant_conversation", "notes", "assistant_conversations", ["originating_conversation_id"], ["id"])
    op.create_foreign_key("fk_notes_assistant_message", "notes", "assistant_messages", ["originating_message_id"], ["id"])
    op.create_foreign_key("fk_context_assistant_conversation", "research_context_items", "assistant_conversations", ["originating_conversation_id"], ["id"])
    op.create_foreign_key("fk_context_assistant_message", "research_context_items", "assistant_messages", ["originating_message_id"], ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM notes WHERE originating_conversation_id IS NOT NULL OR originating_message_id IS NOT NULL) OR EXISTS (SELECT 1 FROM research_context_items WHERE originating_conversation_id IS NOT NULL OR originating_message_id IS NOT NULL)")).scalar():
        raise RuntimeError("assistant provenance exists; refusing destructive downgrade")
    op.drop_constraint("fk_context_assistant_message", "research_context_items", type_="foreignkey")
    op.drop_constraint("fk_context_assistant_conversation", "research_context_items", type_="foreignkey")
    op.drop_constraint("fk_notes_assistant_message", "notes", type_="foreignkey")
    op.drop_constraint("fk_notes_assistant_conversation", "notes", type_="foreignkey")
    for table in ("research_context_items", "notes"):
        op.drop_column(table, "text_origin")
        op.drop_column(table, "originating_message_id")
        op.drop_column(table, "originating_conversation_id")
        op.drop_column(table, "captured_stage")
