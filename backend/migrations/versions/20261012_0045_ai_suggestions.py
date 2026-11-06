"""AI suggestions and their append-only event log (X.46.1).

Revision ID: 20261012_0045
Revises: 20261012_0044
Create Date: 2026-10-12

Additive: `ai_suggestions` (what an AI proposed for one target) and `ai_suggestion_events` (what a person did
with it). The event table is append-only, enforced by an ORM guard (app/suggestion_guard.py) plus a database
trigger installed here, the same pattern as `note_revisions`. Nothing existing changes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261012_0045"
down_revision: Union[str, Sequence[str], None] = "20261012_0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MESSAGE = "ai_suggestion_events is append-only: rows cannot be updated or deleted"


def upgrade() -> None:
    op.create_table(
        "ai_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("target_kind", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("target_field", sa.String(length=60), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("producing_agent", sa.String(length=60), nullable=False),
        sa.Column("guidance", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["ai_suggestions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_suggestions_project_id", "ai_suggestions", ["project_id"])
    op.create_index("ix_ai_suggestions_parent_id", "ai_suggestions", ["parent_id"])
    op.create_index("ix_ai_suggestions_target", "ai_suggestions", ["project_id", "target_kind", "target_id"])

    op.create_table(
        "ai_suggestion_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("suggestion_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("final_text", sa.Text(), nullable=True),
        sa.Column("result_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["suggestion_id"], ["ai_suggestions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("suggestion_id", "seq", name="uq_ai_suggestion_event_seq"),
    )
    op.create_index("ix_ai_suggestion_events_suggestion_id", "ai_suggestion_events", ["suggestion_id"])

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION ai_suggestion_events_append_only() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '{MESSAGE}';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            "CREATE TRIGGER ai_suggestion_events_no_update_delete BEFORE UPDATE OR DELETE ON ai_suggestion_events "
            "FOR EACH ROW EXECUTE FUNCTION ai_suggestion_events_append_only()"
        )
        op.execute(
            "CREATE TRIGGER ai_suggestion_events_no_truncate BEFORE TRUNCATE ON ai_suggestion_events "
            "FOR EACH STATEMENT EXECUTE FUNCTION ai_suggestion_events_append_only()"
        )
    elif dialect == "sqlite":
        for name, verb in (("ai_suggestion_events_no_update", "UPDATE"), ("ai_suggestion_events_no_delete", "DELETE")):
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS {name} BEFORE {verb} ON ai_suggestion_events
                BEGIN
                    SELECT RAISE(ABORT, '{MESSAGE}');
                END
                """
            )


def downgrade() -> None:
    # Refuse to drop either table: pending suggestions may have no event yet, but are still
    # provenance records that must not disappear silently.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM ai_suggestion_events")).scalar():
        raise RuntimeError("ai_suggestion_events still has rows; refusing to drop the record of people's decisions")
    if bind.execute(sa.text("SELECT COUNT(*) FROM ai_suggestions")).scalar():
        raise RuntimeError("ai_suggestions still has rows; refusing to drop AI suggestion provenance")
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS ai_suggestion_events_no_truncate ON ai_suggestion_events")
        op.execute("DROP TRIGGER IF EXISTS ai_suggestion_events_no_update_delete ON ai_suggestion_events")
        op.execute("DROP FUNCTION IF EXISTS ai_suggestion_events_append_only()")
    elif bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS ai_suggestion_events_no_delete")
        op.execute("DROP TRIGGER IF EXISTS ai_suggestion_events_no_update")
    op.drop_index("ix_ai_suggestion_events_suggestion_id", table_name="ai_suggestion_events")
    op.drop_table("ai_suggestion_events")
    op.drop_index("ix_ai_suggestions_target", table_name="ai_suggestions")
    op.drop_index("ix_ai_suggestions_parent_id", table_name="ai_suggestions")
    op.drop_index("ix_ai_suggestions_project_id", table_name="ai_suggestions")
    op.drop_table("ai_suggestions")
