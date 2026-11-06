from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _migrator(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migrate.db').as_posix()}"
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(database_url=url))
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    return config, sa.create_engine(url)


def test_suggestion_downgrade_refuses_pending_suggestions_without_events(tmp_path, monkeypatch):
    config, engine = _migrator(tmp_path, monkeypatch)
    command.upgrade(config, "20261012_0045")

    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id, email) VALUES ('u1', 'owner@example.test')"))
        connection.execute(sa.text("INSERT INTO projects (id, owner_id, title) VALUES ('p1', 'u1', 'Review')"))
        connection.execute(
            sa.text(
                "INSERT INTO ai_suggestions "
                "(id, project_id, target_kind, target_id, content, model_id, prompt_version, producing_agent) "
                "VALUES ('s1', 'p1', 'source', 'source-1', 'AI proposal', 'model', 'prompt@1', 'agent')"
            )
        )

    with pytest.raises(RuntimeError, match="ai_suggestions still has rows"):
        command.downgrade(config, "20261012_0044")

    assert "ai_suggestions" in sa.inspect(engine).get_table_names()
    assert "ai_suggestion_events" in sa.inspect(engine).get_table_names()
    engine.dispose()


def test_empty_suggestion_migration_still_downgrades_and_upgrades(tmp_path, monkeypatch):
    config, engine = _migrator(tmp_path, monkeypatch)
    command.upgrade(config, "20261012_0045")
    command.downgrade(config, "20261012_0044")
    assert "ai_suggestions" not in sa.inspect(engine).get_table_names()
    command.upgrade(config, "20261012_0045")
    assert "ai_suggestions" in sa.inspect(engine).get_table_names()
    engine.dispose()
