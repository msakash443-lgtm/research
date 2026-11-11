"""Research objectives as their own entries; live `objective` context items are copied across (X.193).

Additive: one new table, `research_objectives`. Every live (not retired) `objective` context item is copied in
with its wording, rationale and history. The original context items are left unchanged: `ContextKind.objective`
is kept until the owner decides otherwise (plan rule 44). Downgrading refuses to drop a table that has rows.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261110_0103"
down_revision: Union[str, Sequence[str], None] = "20261109_0102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_objectives",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("research_question_id", sa.Uuid(), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("measure", sa.Text(), nullable=True),
        sa.Column("deliverable", sa.Text(), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_by", sa.String(length=100), nullable=True),
        sa.Column("history", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["research_question_id", "project_id"],
            ["research_questions.id", "research_questions.project_id"],
            name="fk_research_objective_question_same_project",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_objectives_project_id", "research_objectives", ["project_id"])
    op.create_index("ix_research_objectives_research_question_id", "research_objectives", ["research_question_id"])
    op.execute(
        """
        INSERT INTO research_objectives
            (id, project_id, research_question_id, statement, rationale, created_by, history, created_at, updated_at)
        SELECT gen_random_uuid(), project_id, NULL, content, rationale, created_by, history, created_at, updated_at
        FROM research_context_items
        WHERE kind = 'objective' AND retired_at IS NULL
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM research_objectives)")).scalar():
        raise RuntimeError("research_objectives still has rows; the downgrade would destroy objectives (X.193)")
    op.drop_index("ix_research_objectives_research_question_id", table_name="research_objectives")
    op.drop_index("ix_research_objectives_project_id", table_name="research_objectives")
    op.drop_table("research_objectives")
