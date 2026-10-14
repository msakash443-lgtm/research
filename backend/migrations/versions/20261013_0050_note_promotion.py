"""Inbox promotion (X.31.2): gap, claim and memo context kinds, and links back to notes.

Revision ID: 20261013_0050
Revises: 20261012_0042, 20261013_0048, 20261013_0049
Create Date: 2026-10-13

This also merges the three heads the migration graph already had: 20261012_0042 (on the 0041 branch), and
20261013_0048 (ledger storage) and 20261013_0049 (session epoch), which were both written on top of 20261012_0047.
It changes none of them, and it leaves `alembic upgrade head` with a single head again.

Additive. `context_kind` gains `gap`, `claim`, `memo` (Postgres: ALTER TYPE in an autocommit block, as in
20261005_0026; SQLite: VARCHAR with no CHECK constraint, so nothing to do). `research_context_items` gains
`promoted_from_note_id` (the note an item came from) and `related_item_id` (a decision's item, same project).

Downgrade refuses while any gap/claim/memo item exists, or any item still links to a note or another item:
dropping those links is a researcher decision, not something a migration should make silently.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261013_0050"
down_revision: Union[str, Sequence[str], None] = ("20261012_0042", "20261013_0048", "20261013_0049")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_KINDS = ("gap", "claim", "memo")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for kind in NEW_KINDS:
                op.execute(f"ALTER TYPE context_kind ADD VALUE IF NOT EXISTS '{kind}'")
    with op.batch_alter_table("research_context_items") as batch:
        batch.add_column(sa.Column("promoted_from_note_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("related_item_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_context_items_promoted_from_note", "notes", ["promoted_from_note_id"], ["id"])
        batch.create_foreign_key("fk_context_items_related_item", "research_context_items", ["related_item_id"], ["id"])
    op.create_index("ix_research_context_items_promoted_from_note_id", "research_context_items", ["promoted_from_note_id"])
    op.create_index("ix_research_context_items_related_item_id", "research_context_items", ["related_item_id"])


def downgrade() -> None:
    bind = op.get_bind()
    kinds = ", ".join(f"'{kind}'" for kind in NEW_KINDS)
    count = bind.execute(sa.text(f"SELECT COUNT(*) FROM research_context_items WHERE kind IN ({kinds})")).scalar()
    if count:
        raise RuntimeError(f"{count} gap/claim/memo context item(s) exist; delete or re-kind them before downgrading")
    links = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM research_context_items "
            "WHERE promoted_from_note_id IS NOT NULL OR related_item_id IS NOT NULL"
        )
    ).scalar()
    if links:
        raise RuntimeError(f"{links} context item(s) link to a note or another item; unlink them before downgrading")

    op.drop_index("ix_research_context_items_related_item_id", table_name="research_context_items")
    op.drop_index("ix_research_context_items_promoted_from_note_id", table_name="research_context_items")
    with op.batch_alter_table("research_context_items") as batch:
        batch.drop_constraint("fk_context_items_related_item", type_="foreignkey")
        batch.drop_constraint("fk_context_items_promoted_from_note", type_="foreignkey")
        batch.drop_column("related_item_id")
        batch.drop_column("promoted_from_note_id")
    if bind.dialect.name == "postgresql":
        # Rebuild the type without the new values; the 'idea' value (20261005_0026) stays.
        op.execute("ALTER TYPE context_kind RENAME TO context_kind_old")
        op.execute(
            "CREATE TYPE context_kind AS ENUM "
            "('question','objective','hypothesis','methodology','variable','decision','idea')"
        )
        op.execute(
            "ALTER TABLE research_context_items "
            "ALTER COLUMN kind TYPE context_kind USING kind::text::context_kind"
        )
        op.execute("DROP TYPE context_kind_old")
