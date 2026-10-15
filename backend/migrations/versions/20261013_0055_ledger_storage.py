"""Authorship ledger storage: documents, versions and append-only authorship events (X.31.8).

Revision ID: 20261013_0055
Revises: 20261012_0047
Create Date: 2026-10-13

Additive: three new tables. `documents` (one manuscript section; its text lives in versions),
`document_versions` (every saved state, kept forever, Q21) and `authorship_events` (what changed in each
authorship span, by whom and how). The two version and event tables are append-only, enforced by an ORM
guard (app/ledger_guard.py) plus database triggers installed here. Nothing existing changes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261013_0055"
down_revision: Union[str, Sequence[str], None] = "20261012_0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSIONS_MESSAGE = "document_versions is append-only: rows cannot be updated or deleted"
EVENTS_MESSAGE = "authorship_events is append-only: rows cannot be updated or deleted"

AUTHORSHIP_KINDS = ("inserted", "removed", "changed", "resolved", "restored")
SPAN_ORIGINS = ("typed", "dictated", "pasted", "imported", "from_note", "ai_edited", "ai_suggested")


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("ai_locked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_project_id", "documents", ["project_id"])
    op.create_index("ix_documents_current_version_id", "documents", ["current_version_id"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("parent_number", sa.Integer(), nullable=True),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("saved_by", sa.String(length=100), nullable=False),
        sa.Column("client_save_id", sa.String(length=64), nullable=False),
        sa.Column("client_session_id", sa.String(length=64), nullable=True),
        sa.Column("client_saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_from_number", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "number", name="uq_document_version_number"),
        sa.UniqueConstraint("document_id", "client_save_id", name="uq_document_version_client_save"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_project_id", "document_versions", ["project_id"])

    op.create_table(
        "authorship_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("span_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.Enum(*AUTHORSHIP_KINDS, name="authorship_kind"), nullable=False),
        sa.Column("origin", sa.Enum(*SPAN_ORIGINS, name="span_origin"), nullable=False),
        sa.Column("paste_status", sa.String(length=12), nullable=True),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("chars_before", sa.Integer(), nullable=False),
        sa.Column("chars_after", sa.Integer(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("suggestion_id", sa.Uuid(), nullable=True),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("note_id", sa.Uuid(), nullable=True),
        sa.Column("detected_source_id", sa.Uuid(), nullable=True),
        sa.Column("detected_document_id", sa.Uuid(), nullable=True),
        sa.Column("cited_source_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"]),
        sa.ForeignKeyConstraint(["suggestion_id"], ["ai_suggestions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_authorship_events_project_id", "authorship_events", ["project_id"])
    op.create_index("ix_authorship_events_document_id", "authorship_events", ["document_id"])
    op.create_index("ix_authorship_events_version_id", "authorship_events", ["version_id"])
    op.create_index("ix_authorship_events_span_id", "authorship_events", ["span_id"])
    op.create_index("ix_authorship_events_suggestion_id", "authorship_events", ["suggestion_id"])

    _install_append_only("document_versions", VERSIONS_MESSAGE)
    _install_append_only("authorship_events", EVENTS_MESSAGE)


def downgrade() -> None:
    # Refuse to drop the record of what was written and who wrote it while it still has rows.
    bind = op.get_bind()
    for table in ("authorship_events", "document_versions"):
        if bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError(f"{table} still has rows; refusing to drop the authorship record")

    _remove_append_only("authorship_events")
    _remove_append_only("document_versions")
    op.drop_index("ix_authorship_events_suggestion_id", table_name="authorship_events")
    op.drop_index("ix_authorship_events_span_id", table_name="authorship_events")
    op.drop_index("ix_authorship_events_version_id", table_name="authorship_events")
    op.drop_index("ix_authorship_events_document_id", table_name="authorship_events")
    op.drop_index("ix_authorship_events_project_id", table_name="authorship_events")
    op.drop_table("authorship_events")
    op.drop_index("ix_document_versions_project_id", table_name="document_versions")
    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index("ix_documents_current_version_id", table_name="documents")
    op.drop_index("ix_documents_project_id", table_name="documents")
    op.drop_table("documents")
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS authorship_kind")
        op.execute("DROP TYPE IF EXISTS span_origin")


def _install_append_only(table: str, message: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {table}_append_only() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '{message}';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {table}_append_only()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {table}_append_only()"
        )
    elif dialect == "sqlite":
        for suffix, verb in (("no_update", "UPDATE"), ("no_delete", "DELETE")):
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS {table}_{suffix} BEFORE {verb} ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{message}');
                END
                """
            )


def _remove_append_only(table: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_append_only()")
    elif dialect == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
