"""Add research questions and hypotheses linked to curated gaps (M4.1)."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261014_0054"
down_revision: Union[str, Sequence[str], None] = "20261013_0053"
branch_labels = None
depends_on = None


def _create_project_guard(table: str, left_table: str, left_column: str, right_table: str | None, right_column: str | None, message: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for event in ("INSERT", "UPDATE"):
            right_check = "" if right_table is None else f"\n                  OR NOT EXISTS (SELECT 1 FROM {right_table} WHERE id = NEW.{right_column} AND project_id = NEW.project_id)"
            op.execute(f"""
                CREATE TRIGGER {table}_same_project_{event.lower()}
                BEFORE {event} ON {table}
                WHEN NOT EXISTS (SELECT 1 FROM {left_table} WHERE id = NEW.{left_column} AND project_id = NEW.project_id)
                  {right_check}
                BEGIN SELECT RAISE(ABORT, '{message}'); END
            """)
    elif bind.dialect.name == "postgresql":
        right_check = "" if right_table is None else f"\n                   OR NOT EXISTS (SELECT 1 FROM {right_table} WHERE id = NEW.{right_column} AND project_id = NEW.project_id)"
        op.execute(f"""
            CREATE FUNCTION {table}_same_project_guard() RETURNS trigger AS $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM {left_table} WHERE id = NEW.{left_column} AND project_id = NEW.project_id)
                   {right_check} THEN
                    RAISE EXCEPTION '{message}';
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql
        """)
        op.execute(f"CREATE TRIGGER {table}_same_project BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {table}_same_project_guard()")


def _drop_project_guard(table: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_same_project_update")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_same_project_insert")
    elif bind.dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {table}_same_project ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_same_project_guard()")


def upgrade() -> None:
    op.create_table(
        "research_questions",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False), sa.Column("type", sa.String(30), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]), sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "text", name="uq_research_question_project_text"),
        sa.UniqueConstraint("id", "project_id", name="uq_research_question_id_project"),
    )
    op.create_index("ix_research_questions_project_id", "research_questions", ["project_id"])
    op.create_table(
        "research_question_gaps",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("research_question_id", sa.Uuid(), nullable=False), sa.Column("gap_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["research_question_id", "project_id"], ["research_questions.id", "research_questions.project_id"]),
        sa.ForeignKeyConstraint(["gap_id"], ["gaps.id"]),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("research_question_id", "gap_id", name="uq_research_question_gap"),
    )
    op.create_index("ix_research_question_gaps_project_id", "research_question_gaps", ["project_id"])
    op.create_index("ix_research_question_gaps_research_question_id", "research_question_gaps", ["research_question_id"])
    op.create_index("ix_research_question_gaps_gap_id", "research_question_gaps", ["gap_id"])
    _create_project_guard("research_question_gaps", "research_questions", "research_question_id", "gaps", "gap_id", "research question gap link must belong to one project")
    op.create_table(
        "hypotheses",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("research_question_id", sa.Uuid(), nullable=False), sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("direction", sa.String(30), nullable=False), sa.Column("theoretical_basis", sa.Text(), nullable=False), sa.Column("empirical_support", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["research_question_id", "project_id"], ["research_questions.id", "research_questions.project_id"]),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("project_id", "statement", name="uq_hypothesis_project_statement"),
    )
    op.create_index("ix_hypotheses_project_id", "hypotheses", ["project_id"])
    op.create_index("ix_hypotheses_research_question_id", "hypotheses", ["research_question_id"])
    _create_project_guard("hypotheses", "research_questions", "research_question_id", None, None, "hypothesis must belong to its research question project")


def downgrade() -> None:
    _drop_project_guard("hypotheses")
    op.drop_index("ix_hypotheses_research_question_id", table_name="hypotheses")
    op.drop_index("ix_hypotheses_project_id", table_name="hypotheses")
    op.drop_table("hypotheses")
    _drop_project_guard("research_question_gaps")
    op.drop_index("ix_research_question_gaps_gap_id", table_name="research_question_gaps")
    op.drop_index("ix_research_question_gaps_research_question_id", table_name="research_question_gaps")
    op.drop_index("ix_research_question_gaps_project_id", table_name="research_question_gaps")
    op.drop_table("research_question_gaps")
    op.drop_index("ix_research_questions_project_id", table_name="research_questions")
    op.drop_table("research_questions")
