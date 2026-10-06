"""Record where a source came from: sources.origin ('manual' | 'retrieved').

Revision ID: 20261004_0019
Revises: 20261003_0018
Create Date: 2026-10-04

`Source.is_automated` used to be inferred from `source_type`, which a connector with a new type
name would have escaped. Origin is now explicit and set only by the system. Existing sources get
`retrieved` when their type is one ARC produces, else `manual`.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261004_0019"
down_revision: Union[str, Sequence[str], None] = "20261003_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ARC_TYPES = ("web_search", "scholar", "crawled_page", "pdf_extract")  # frozen copy: migrations don't import app code


def upgrade() -> None:
    op.add_column("sources", sa.Column("origin", sa.String(length=20), nullable=False, server_default="manual"))
    sources = sa.table("sources", sa.column("origin", sa.String), sa.column("source_type", sa.String))
    op.execute(sources.update().where(sources.c.source_type.in_(ARC_TYPES)).values(origin="retrieved"))


def downgrade() -> None:
    op.drop_column("sources", "origin")
