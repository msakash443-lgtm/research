"""Add 'idea' to the context_kind enum (quick-capture research ideas; never sent to the agent).

Revision ID: 20261005_0026
Revises: 20261005_0025
Create Date: 2026-10-05

First migration in this repo to add a value to an already-existing Postgres enum type
(`context_kind`, created once in 20260907_0001 and never altered since). Postgres can't run
`ALTER TYPE ... ADD VALUE` inside the same transaction as later use of that value, so it runs in
an autocommit block. On SQLite, `research_context_items.kind` is a plain VARCHAR(11) with no CHECK
constraint (verified against the dev DB), so 'idea' (4 chars) fits with no DDL change there.

Downgrade refuses to run while any 'idea' row exists: re-kinding or deleting those rows is a
researcher decision, not something a migration should silently make for them.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261005_0026"
down_revision: Union[str, Sequence[str], None] = "20261005_0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE context_kind ADD VALUE IF NOT EXISTS 'idea'")
    # SQLite: kind is a plain VARCHAR(11), no CHECK constraint; 'idea' fits, no DDL needed.


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM research_context_items WHERE kind = 'idea'")).scalar()
    if count:
        raise RuntimeError(
            f"{count} idea context item(s) exist; delete or re-kind them before downgrading"
        )
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE context_kind RENAME TO context_kind_old")
        op.execute(
            "CREATE TYPE context_kind AS ENUM "
            "('question','objective','hypothesis','methodology','variable','decision')"
        )
        op.execute(
            "ALTER TABLE research_context_items "
            "ALTER COLUMN kind TYPE context_kind USING kind::text::context_kind"
        )
        op.execute("DROP TYPE context_kind_old")
    # SQLite: no DDL was made in upgrade(), so nothing to undo.
