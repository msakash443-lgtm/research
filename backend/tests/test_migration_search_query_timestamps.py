from pathlib import Path
from types import SimpleNamespace

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


def _defaults(engine) -> dict[str, str | None]:
    columns = {column["name"]: column for column in sa.inspect(engine).get_columns("search_queries")}
    return {name: columns[name]["default"] for name in ("created_at", "updated_at")}


def test_search_query_timestamp_migration_adds_and_removes_server_defaults(tmp_path, monkeypatch):
    config, engine = _migrator(tmp_path, monkeypatch)
    command.upgrade(config, "20261022_0097")
    # 0098 alters unrelated criteria/seed timestamps with PostgreSQL-only DDL; mark it applied so this
    # isolated test can execute X.151's migration against the same predecessor schema.
    command.stamp(config, "20261027_0098")
    assert _defaults(engine) == {"created_at": None, "updated_at": None}

    command.upgrade(config, "20261029_0099")
    assert all(_defaults(engine).values())

    command.downgrade(config, "20261027_0098")
    assert _defaults(engine) == {"created_at": None, "updated_at": None}

    command.upgrade(config, "20261029_0099")
    assert all(_defaults(engine).values())
    engine.dispose()
