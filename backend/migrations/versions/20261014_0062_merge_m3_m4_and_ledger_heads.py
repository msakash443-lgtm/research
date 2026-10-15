"""Merge the M3/M4 branch and the ledger/workspace branch into a single head.

Revision ID: 20261014_0062
Revises: 20261014_0054, 20261014_0061
Create Date: 2026-10-15

Repo history grew two parallel migration chains that both reused the same `NNNN` numbers
(20261013_0048 through 20261014_0054), produced independently by different task sessions
working from the same head without seeing each other's new files. The duplicate-id files
have been renamed/renumbered to a single ascending sequence (ledger storage 0048 -> 0055,
session epoch 0049 -> 0056, the note-promotion merge 0050 -> 0057 (updated to depend on the
renamed 0055/0056), workspace AI suggestions 0051 -> 0058, similarity checks 0052 -> 0059,
provenance reports 0053 -> 0060, user erased_at 0054 -> 0061); no revision's upgrade/downgrade
body changed. This migration only joins the two resulting heads (M4.1's
`20261014_0054_research_questions_hypotheses` and the renamed
`20261014_0061_user_erased_at`) so `alembic upgrade head` has exactly one head again. It
changes no schema.
"""

from typing import Sequence, Union


revision: str = "20261014_0062"
down_revision: Union[str, Sequence[str], None] = ("20261014_0054", "20261014_0061")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
