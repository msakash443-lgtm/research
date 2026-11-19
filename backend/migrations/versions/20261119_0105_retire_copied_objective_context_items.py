"""Retire the legacy `objective` context items now that `research_objectives` is the only active path (X.193).

Migration 0103 copied live `objective` context items into `research_objectives` but left the originals live,
so a retired copy still left an active legacy objective behind. This copies any live objective context item
that was written after 0103 and was therefore never copied, then retires every live one (nothing is deleted;
the `ContextKind.objective` enum value stays for historical rows). Retired rows are marked with
`retired_by = 'migration:20261119_0105'`, which is how the downgrade finds exactly the rows to bring back.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20261119_0105"
down_revision: Union[str, Sequence[str], None] = "20261112_0104"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MARKER = "migration:20261119_0105"


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO research_objectives
            (id, project_id, research_question_id, statement, rationale, created_by, history, created_at, updated_at)
        SELECT gen_random_uuid(), c.project_id, NULL, c.content, c.rationale, c.created_by, c.history, c.created_at, c.updated_at
        FROM research_context_items c
        WHERE c.kind = 'objective' AND c.retired_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM research_objectives o
              WHERE o.project_id = c.project_id AND o.statement = c.content AND o.created_at = c.created_at
          )
        """
    )
    op.execute(
        f"""
        UPDATE research_context_items
        SET retired_at = now(), retired_by = '{MARKER}'
        WHERE kind = 'objective' AND retired_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE research_context_items
        SET retired_at = NULL, retired_by = NULL
        WHERE kind = 'objective' AND retired_by = '{MARKER}'
        """
    )
