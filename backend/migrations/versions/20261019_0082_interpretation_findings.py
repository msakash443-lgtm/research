"""Add interpretation_findings and interpretation_reviews: the findings-to-literature map (one computed
agree/disagree/unclear classification per analysis-run finding) and the human reviews that confirm or
dispute each one, required before G9 can be approved (M6.9, spec 5.10 / 8).

Revision ID: 20261019_0082
Revises: 20261019_0081
Create Date: 2026-10-19

Additive: two new tables. Append-only, same two-layer pattern as section_approvals (migration
20261019_0081): an ORM guard (app/ledger_guard.py) plus database triggers installed here. Nothing
existing changes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261019_0082"
down_revision: Union[str, Sequence[str], None] = "20261019_0081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FINDINGS_MESSAGE = "interpretation_findings is append-only: rows cannot be updated or deleted"
REVIEWS_MESSAGE = "interpretation_reviews is append-only: rows cannot be updated or deleted"


def upgrade() -> None:
    op.create_table(
        "interpretation_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("analysis_run_id", sa.Uuid(), nullable=False),
        sa.Column("output_key", sa.String(length=200), nullable=False),
        sa.Column("output_path", sa.String(length=500), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("classification", sa.String(length=10), nullable=False),
        sa.Column("literature_source_ids", sa.JSON(), nullable=False),
        sa.Column("literature_extraction_ids", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("generated_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["analysis_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_interpretation_findings_project_id", "interpretation_findings", ["project_id"])
    op.create_index("ix_interpretation_findings_batch_id", "interpretation_findings", ["batch_id"])
    op.create_index("ix_interpretation_findings_generation", "interpretation_findings", ["generation"])
    op.create_index("ix_interpretation_findings_analysis_run_id", "interpretation_findings", ["analysis_run_id"])
    _install_append_only("interpretation_findings", FINDINGS_MESSAGE)

    op.create_table(
        "interpretation_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=12), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["finding_id"], ["interpretation_findings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("finding_id", "seq", name="uq_interpretation_review_seq"),
    )
    op.create_index("ix_interpretation_reviews_project_id", "interpretation_reviews", ["project_id"])
    op.create_index("ix_interpretation_reviews_finding_id", "interpretation_reviews", ["finding_id"])
    _install_append_only("interpretation_reviews", REVIEWS_MESSAGE)


def downgrade() -> None:
    # Refuse to drop recorded findings/reviews while any exist: they are the record of what was computed
    # and who confirmed it, same convention as section_approvals.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM interpretation_reviews")).scalar():
        raise RuntimeError("interpretation_reviews still has rows; refusing to drop the review record")
    if bind.execute(sa.text("SELECT COUNT(*) FROM interpretation_findings")).scalar():
        raise RuntimeError("interpretation_findings still has rows; refusing to drop the map record")

    _remove_append_only("interpretation_reviews")
    op.drop_index("ix_interpretation_reviews_finding_id", table_name="interpretation_reviews")
    op.drop_index("ix_interpretation_reviews_project_id", table_name="interpretation_reviews")
    op.drop_table("interpretation_reviews")

    _remove_append_only("interpretation_findings")
    op.drop_index("ix_interpretation_findings_analysis_run_id", table_name="interpretation_findings")
    op.drop_index("ix_interpretation_findings_generation", table_name="interpretation_findings")
    op.drop_index("ix_interpretation_findings_batch_id", table_name="interpretation_findings")
    op.drop_index("ix_interpretation_findings_project_id", table_name="interpretation_findings")
    op.drop_table("interpretation_findings")


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
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_append_only()")
    elif dialect == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")
