import uuid

import pytest
import sqlalchemy as sa
from alembic import command


def test_suggestion_downgrade_refuses_pending_suggestions_without_events(migrator):
    config, engine = migrator
    command.upgrade(config, "20261012_0045")
    owner, project = uuid.uuid4(), uuid.uuid4()

    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id, email) VALUES (:id, 'owner@example.test')"), {"id": owner})
        connection.execute(
            sa.text("INSERT INTO projects (id, owner_id, title) VALUES (:id, :owner, 'Review')"),
            {"id": project, "owner": owner},
        )
        connection.execute(
            sa.text(
                "INSERT INTO ai_suggestions "
                "(id, project_id, target_kind, target_id, content, model_id, prompt_version, producing_agent) "
                "VALUES (:id, :project, 'source', 'source-1', 'AI proposal', 'model', 'prompt@1', 'agent')"
            ),
            {"id": uuid.uuid4(), "project": project},
        )

    with pytest.raises(RuntimeError, match="ai_suggestions still has rows"):
        command.downgrade(config, "20261012_0044")

    assert "ai_suggestions" in sa.inspect(engine).get_table_names()
    assert "ai_suggestion_events" in sa.inspect(engine).get_table_names()


def test_empty_suggestion_migration_still_downgrades_and_upgrades(migrator):
    config, engine = migrator
    command.upgrade(config, "20261012_0045")
    command.downgrade(config, "20261012_0044")
    assert "ai_suggestions" not in sa.inspect(engine).get_table_names()
    command.upgrade(config, "20261012_0045")
    assert "ai_suggestions" in sa.inspect(engine).get_table_names()
