from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_conceptual_paths_downgrade_refuses_to_drop_researcher_paths(tmp_path, monkeypatch):
    """A downgrade must not silently destroy person-drawn conceptual paths (M4.2 review)."""
    url = f"sqlite:///{(tmp_path / 'migrate.db').as_posix()}"
    # env.py reads the URL from get_settings(); leave the shared settings cache untouched.
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(database_url=url))
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    engine = sa.create_engine(url)

    command.upgrade(config, "20261015_0065")
    # SQLite does not enforce FKs on this raw connection, so one row is enough to stand for a drawn path.
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO conceptual_paths (id, project_id, from_construct_id, to_construct_id, kind, "
            "source_ids, rationale, created_by) VALUES ('p1', 'proj', 'a', 'b', 'direct', '[]', 'why', 'alice')"))

    with pytest.raises(RuntimeError, match="conceptual_paths still has rows"):
        command.downgrade(config, "20261015_0064")
    assert "conceptual_paths" in sa.inspect(engine).get_table_names()

    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM conceptual_paths"))
    command.downgrade(config, "20261015_0064")
    assert "conceptual_paths" not in sa.inspect(engine).get_table_names()
    engine.dispose()
