"""Add project construct and theory inventories (M3.7)."""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20261013_0048"
down_revision: Union[str, Sequence[str], None] = "20261012_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "constructs",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("measures", sa.JSON(), nullable=False), sa.Column("author", sa.String(200), nullable=False),
        sa.Column("jingle_jangle_flags", sa.JSON(), nullable=False), sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]), sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", "author", name="uq_construct_project_name_author"),
        sa.UniqueConstraint("id", "project_id", name="uq_construct_id_project"),
    )
    op.create_index("ix_constructs_project_id", "constructs", ["project_id"])
    op.create_table(
        "theories",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]), sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_theory_project_name"),
        sa.UniqueConstraint("id", "project_id", name="uq_theory_id_project"),
    )
    op.create_index("ix_theories_project_id", "theories", ["project_id"])
    op.create_table(
        "theory_constructs",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("theory_id", sa.Uuid(), nullable=False), sa.Column("construct_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["theory_id", "project_id"], ["theories.id", "theories.project_id"]),
        sa.ForeignKeyConstraint(["construct_id", "project_id"], ["constructs.id", "constructs.project_id"]),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("theory_id", "construct_id", name="uq_theory_construct"),
    )
    op.create_index("ix_theory_constructs_project_id", "theory_constructs", ["project_id"])
    op.create_index("ix_theory_constructs_theory_id", "theory_constructs", ["theory_id"])
    op.create_index("ix_theory_constructs_construct_id", "theory_constructs", ["construct_id"])
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        for event in ("INSERT", "UPDATE"):
            op.execute(f"""
                CREATE TRIGGER theory_constructs_same_project_{event.lower()}
                BEFORE {event} ON theory_constructs
                WHEN NOT EXISTS (SELECT 1 FROM theories WHERE id = NEW.theory_id AND project_id = NEW.project_id)
                  OR NOT EXISTS (SELECT 1 FROM constructs WHERE id = NEW.construct_id AND project_id = NEW.project_id)
                BEGIN SELECT RAISE(ABORT, 'theory construct link must belong to one project'); END
            """)
    elif bind.dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION theory_constructs_same_project_guard() RETURNS trigger AS $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM theories WHERE id = NEW.theory_id AND project_id = NEW.project_id)
                   OR NOT EXISTS (SELECT 1 FROM constructs WHERE id = NEW.construct_id AND project_id = NEW.project_id) THEN
                    RAISE EXCEPTION 'theory construct link must belong to one project';
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql
        """)
        op.execute("CREATE TRIGGER theory_constructs_same_project BEFORE INSERT OR UPDATE ON theory_constructs FOR EACH ROW EXECUTE FUNCTION theory_constructs_same_project_guard()")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS theory_constructs_same_project_update")
        op.execute("DROP TRIGGER IF EXISTS theory_constructs_same_project_insert")
    elif bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS theory_constructs_same_project ON theory_constructs")
        op.execute("DROP FUNCTION IF EXISTS theory_constructs_same_project_guard()")
    op.drop_index("ix_theory_constructs_construct_id", table_name="theory_constructs")
    op.drop_index("ix_theory_constructs_theory_id", table_name="theory_constructs")
    op.drop_index("ix_theory_constructs_project_id", table_name="theory_constructs")
    op.drop_table("theory_constructs")
    op.drop_index("ix_theories_project_id", table_name="theories")
    op.drop_table("theories")
    op.drop_index("ix_constructs_project_id", table_name="constructs")
    op.drop_table("constructs")
