"""Notes: per-user quick capture (X.31.1).

Revision ID: 20261006_0028
Revises: 20261005_0027
Create Date: 2026-10-06

Additive. `notes` is an ordinary mutable table (edits write a new `note_revisions` row and
bump `notes.revision`/`notes.body`). `note_revisions` is append-only, enforced the same way
as `audit_events`: an ORM guard (app/note_guard.py) plus a database trigger installed here,
so raw SQL and other writers are blocked too, not just the application.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261006_0028"
down_revision: Union[str, Sequence[str], None] = "20261005_0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

note_kind = sa.Enum("typed", "voice", "clip", "highlight", "photo", name="note_kind")
note_status = sa.Enum("inbox", "filed", "archived", name="note_status")


def upgrade() -> None:
    bind = op.get_bind()
    note_kind.create(bind, checkfirst=True)
    note_status.create(bind, checkfirst=True)

    op.create_table(
        "notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("kind", note_kind.with_variant(postgresql.ENUM("typed", "voice", "clip", "highlight", "photo", name="note_kind", create_type=False), "postgresql"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("quoted_text", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(length=2000), nullable=True),
        sa.Column("locator", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            note_status.with_variant(postgresql.ENUM("inbox", "filed", "archived", name="note_status", create_type=False), "postgresql"),
            nullable=False,
            server_default="inbox",
        ),
        sa.Column("ai_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "client_id", name="uq_note_owner_client"),
    )
    op.create_index("ix_notes_owner_id", "notes", ["owner_id"])
    op.create_index("ix_notes_project_id", "notes", ["project_id"])

    op.create_table(
        "note_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("quoted_text", sa.Text(), nullable=True),
        sa.Column("edited_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("note_id", "revision", name="uq_note_revision"),
    )
    op.create_index("ix_note_revisions_note_id", "note_revisions", ["note_id"])

    # Append-only enforcement for note_revisions (mirrors 20261003_0006_audit_events_append_only.py).
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION note_revisions_append_only() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'note_revisions is append-only: rows cannot be updated or deleted';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            "CREATE TRIGGER note_revisions_no_update_delete BEFORE UPDATE OR DELETE ON note_revisions "
            "FOR EACH ROW EXECUTE FUNCTION note_revisions_append_only()"
        )
        op.execute(
            "CREATE TRIGGER note_revisions_no_truncate BEFORE TRUNCATE ON note_revisions "
            "FOR EACH STATEMENT EXECUTE FUNCTION note_revisions_append_only()"
        )
    elif dialect == "sqlite":
        for name, verb in (("note_revisions_no_update", "UPDATE"), ("note_revisions_no_delete", "DELETE")):
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS {name} BEFORE {verb} ON note_revisions
                BEGIN
                    SELECT RAISE(ABORT, 'note_revisions is append-only: rows cannot be updated or deleted');
                END
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS note_revisions_no_truncate ON note_revisions")
        op.execute("DROP TRIGGER IF EXISTS note_revisions_no_update_delete ON note_revisions")
        op.execute("DROP FUNCTION IF EXISTS note_revisions_append_only()")
    elif bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS note_revisions_no_delete")
        op.execute("DROP TRIGGER IF EXISTS note_revisions_no_update")

    op.drop_index("ix_note_revisions_note_id", table_name="note_revisions")
    op.drop_table("note_revisions")
    op.drop_index("ix_notes_project_id", table_name="notes")
    op.drop_index("ix_notes_owner_id", table_name="notes")
    op.drop_table("notes")

    note_status.drop(bind, checkfirst=True)
    note_kind.drop(bind, checkfirst=True)
