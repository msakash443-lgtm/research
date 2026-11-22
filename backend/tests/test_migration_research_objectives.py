import uuid

import pytest
import sqlalchemy as sa
from alembic import command

pytestmark = pytest.mark.slow  # walks the Alembic chain


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


def test_a_populated_upgrade_downgrade_upgrade_round_trip_keeps_the_context_objective_and_its_copy(migrator):
    """One live context objective is copied, retired in its place, restored on downgrade and copied again (X.193)."""
    config, engine = migrator
    command.upgrade(config, "20261109_0102")
    owner, project, item = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id, email) VALUES (:id, 'owner@example.test')"), {"id": owner})
        connection.execute(
            sa.text("INSERT INTO projects (id, owner_id, title) VALUES (:id, :owner, 'Objectives')"),
            {"id": project, "owner": owner},
        )
        _seed_context(connection, project, item, "Map remote-work productivity", retired=False)

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT count(*) FROM research_objectives")).scalar() == 1
        retired_at, retired_by = connection.execute(
            sa.text("SELECT retired_at, retired_by FROM research_context_items WHERE id = :id"), {"id": item}
        ).one()
    assert retired_at is not None and retired_by.startswith("migration:")

    command.downgrade(config, "20261109_0102")
    with engine.connect() as connection:
        assert "research_objectives" not in sa.inspect(connection).get_table_names()
        assert connection.execute(
            sa.text("SELECT retired_at FROM research_context_items WHERE id = :id"), {"id": item}
        ).scalar() is None

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT count(*) FROM research_objectives")).scalar() == 1


def test_a_context_objective_written_after_the_copy_migration_is_copied_once_and_then_retired(migrator):
    config, engine = migrator
    command.upgrade(config, "20261112_0104")
    owner, project, late = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id, email) VALUES (:id, 'owner@example.test')"), {"id": owner})
        connection.execute(
            sa.text("INSERT INTO projects (id, owner_id, title) VALUES (:id, :owner, 'Objectives')"),
            {"id": project, "owner": owner},
        )
        _seed_context(connection, project, late, "Written after the first copy", retired=False)

    command.upgrade(config, "head")
    with engine.connect() as connection:
        statements = connection.execute(sa.text("SELECT statement FROM research_objectives")).scalars().all()
        live_legacy = connection.execute(
            sa.text("SELECT count(*) FROM research_context_items WHERE kind = 'objective' AND retired_at IS NULL")
        ).scalar()
    assert statements == ["Written after the first copy"]
    assert live_legacy == 0
