import uuid

import pytest
import sqlalchemy as sa
from alembic import command


def _seed_context(connection, project, item_id, content, retired):
    connection.execute(
        sa.text(
            "INSERT INTO research_context_items (id, project_id, kind, content, created_by, retired_at) "
            "VALUES (:id, :project, 'objective', :content, 'alice', :retired)"
        ),
        {"id": item_id, "project": project, "content": content, "retired": "2026-11-01T00:00:00+00:00" if retired else None},
    )


def test_research_objectives_migration_copies_only_live_objective_context_items(migrator):
    """Every live `objective` context item becomes a research objective; retired ones stay behind (X.193)."""
    config, engine = migrator
    command.upgrade(config, "20261109_0102")
    owner, project = uuid.uuid4(), uuid.uuid4()
    live, retired = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id, email) VALUES (:id, 'owner@example.test')"), {"id": owner})
        connection.execute(
            sa.text("INSERT INTO projects (id, owner_id, title) VALUES (:id, :owner, 'Objectives')"),
            {"id": project, "owner": owner},
        )
        _seed_context(connection, project, live, "Map remote-work productivity", retired=False)
        _seed_context(connection, project, retired, "An abandoned aim", retired=True)

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.execute(
            sa.text("SELECT statement, created_by FROM research_objectives WHERE project_id = :project"), {"project": project}
        ).all()
    assert [tuple(row) for row in rows] == [("Map remote-work productivity", "alice")]


def test_research_objectives_downgrade_refuses_to_drop_rows_then_round_trips(migrator):
    config, engine = migrator
    command.upgrade(config, "head")
    owner, project = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id, email) VALUES (:id, 'owner@example.test')"), {"id": owner})
        connection.execute(
            sa.text("INSERT INTO projects (id, owner_id, title) VALUES (:id, :owner, 'Objectives')"),
            {"id": project, "owner": owner},
        )
        connection.execute(
            sa.text("INSERT INTO research_objectives (id, project_id, statement, created_by) VALUES (:id, :project, 'Aim', 'alice')"),
            {"id": uuid.uuid4(), "project": project},
        )

    with pytest.raises(RuntimeError, match="research_objectives still has rows"):
        command.downgrade(config, "20261109_0102")
    assert "research_objectives" in sa.inspect(engine).get_table_names()

    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM research_objectives"))
    command.downgrade(config, "20261109_0102")
    assert "research_objectives" not in sa.inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "research_objectives" in sa.inspect(engine).get_table_names()
