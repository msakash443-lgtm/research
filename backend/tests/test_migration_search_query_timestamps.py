import sqlalchemy as sa
from uuid import uuid4

from sqlalchemy import text

from alembic import command


def _defaults(engine) -> dict[str, str | None]:
    columns = {column["name"]: column for column in sa.inspect(engine).get_columns("search_queries")}
    return {name: columns[name]["default"] for name in ("created_at", "updated_at")}


def test_search_query_timestamp_migration_adds_and_removes_server_defaults(migrator):
    config, engine = migrator
    command.upgrade(config, "20261027_0098")
    assert _defaults(engine) == {"created_at": None, "updated_at": None}

    command.upgrade(config, "20261029_0099")
    assert all(_defaults(engine).values())

    command.downgrade(config, "20261027_0098")
    assert _defaults(engine) == {"created_at": None, "updated_at": None}

    command.upgrade(config, "20261029_0099")
    assert all(_defaults(engine).values())


def test_meta_analysis_plan_timestamp_migration_inserts_with_database_generated_timestamps(migrator):
    config, engine = migrator
    command.upgrade(config, "20261029_0100")
    before = {column["name"]: column["default"] for column in sa.inspect(engine).get_columns("meta_analysis_plans")}
    assert before["created_at"] is None and before["updated_at"] is None

    command.upgrade(config, "20261029_0101")
    owner_id = uuid4()
    project_id = uuid4()
    plan_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email) VALUES (:id, :email)"),
            {"id": owner_id, "email": f"{owner_id}@example.invalid"},
        )
        connection.execute(
            text("INSERT INTO projects (id, owner_id, title) VALUES (:id, :owner_id, :title)"),
            {"id": project_id, "owner_id": owner_id, "title": "Timestamp migration test"},
        )
        timestamps = connection.execute(
            text(
                "INSERT INTO meta_analysis_plans "
                "(id, project_id, outcome, primary_model, studies_json, created_by) "
                "VALUES (:id, :project_id, :outcome, :primary_model, :studies_json, :created_by) "
                "RETURNING created_at, updated_at"
            ),
            {
                "id": plan_id,
                "project_id": project_id,
                "outcome": "test outcome",
                "primary_model": "FE",
                "studies_json": "[]",
                "created_by": "migration-test",
            },
        ).one()
        assert timestamps.created_at is not None and timestamps.updated_at is not None
        connection.execute(text("DELETE FROM meta_analysis_plans WHERE id = :id"), {"id": plan_id})

    command.downgrade(config, "20261029_0100")
    after = {column["name"]: column["default"] for column in sa.inspect(engine).get_columns("meta_analysis_plans")}
    assert after["created_at"] is None and after["updated_at"] is None
