"""Workspace AI side panel (X.31.11): the action, input hash and input size on a shared AI suggestion.

Revision ID: 20261013_0058
Revises: 20261013_0057
Create Date: 2026-10-13

Additive. The side panel reuses the shared `ai_suggestions` table (X.46.1) rather than a second table. Three
nullable columns say which workspace action produced a suggestion and what it was given: `action` (one of
challenge, find_evidence, clearer_sentence, argument_check), `input_hash` (sha256 of the selected text as
sent; the text itself is never stored on a suggestion) and `input_chars`. Suggestions from other features
leave them NULL.

Downgrade refuses while any workspace suggestion exists: dropping the action record would make those
suggestions unexplainable, and that is a researcher-visible loss.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261013_0058"
down_revision: Union[str, Sequence[str], None] = "20261013_0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_suggestions") as batch:
        batch.add_column(sa.Column("action", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("input_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("input_chars", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    in_use = bind.execute(sa.text("SELECT COUNT(*) FROM ai_suggestions WHERE action IS NOT NULL")).scalar()
    if in_use:
        raise RuntimeError(
            f"Refusing to downgrade: {in_use} workspace AI suggestion(s) record their action. "
            "Decide what to do with them first; this migration does not delete them."
        )
    with op.batch_alter_table("ai_suggestions") as batch:
        batch.drop_column("input_chars")
        batch.drop_column("input_hash")
        batch.drop_column("action")
