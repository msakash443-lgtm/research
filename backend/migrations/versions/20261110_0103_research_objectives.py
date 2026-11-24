"""Research objectives as their own entries; live `objective` context items are copied across (X.193).

Additive: one new table, `research_objectives`. Every live (not retired) `objective` context item is copied in
with its wording, rationale and history. The original context items are left unchanged: `ContextKind.objective`
is kept (owner decision). Downgrading drops rows that are still verbatim copies of a context item and refuses
when any other row remains.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261110_0103"
down_revision: Union[str, Sequence[str], None] = "20261109_0102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("research_objectives"):
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
    index_names = {index["name"] for index in sa.inspect(bind).get_indexes("research_objectives")}
    if "ix_research_objectives_project_id" not in index_names:
        op.create_index("ix_research_objectives_project_id", "research_objectives", ["project_id"])
    if "ix_research_objectives_research_question_id" not in index_names:
        op.create_index("ix_research_objectives_research_question_id", "research_objectives", ["research_question_id"])
    op.execute(
        """
        INSERT INTO research_objectives
            (id, project_id, research_question_id, statement, rationale, created_by, history, created_at, updated_at)
        SELECT gen_random_uuid(), project_id, NULL, content, rationale, created_by, history, created_at, updated_at
        FROM research_context_items
        WHERE kind = 'objective' AND retired_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM research_objectives existing
              WHERE existing.project_id = research_context_items.project_id
                AND existing.statement = research_context_items.content
          )
        """
    )

def downgrade() -> None:
    connection = op.get_bind()
    # Rows that are still verbatim copies of a context item lose nothing when dropped (the context item
    # stays); any other row (written or amended through the objectives route) refuses the downgrade.
    connection.execute(
        sa.text(
            """
            DELETE FROM research_objectives o
            USING research_context_items c
            WHERE c.kind = 'objective' AND c.project_id = o.project_id AND c.content = o.statement
              AND c.created_at = o.created_at AND o.retired_at IS NULL AND o.history::text IS NOT DISTINCT FROM c.history::text
            """
        )
    )
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM research_objectives)")).scalar():
        raise RuntimeError("research_objectives still has rows; the downgrade would destroy objectives (X.193)")
    op.drop_index("ix_research_objectives_research_question_id", table_name="research_objectives")
    op.drop_index("ix_research_objectives_project_id", table_name="research_objectives")
    op.drop_table("research_objectives")
