"""The whole Alembic chain, run for real once (the other migration tests start from a cached, already migrated copy)."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


@pytest.mark.slow
def test_the_whole_chain_upgrades_checks_and_downgrades_on_an_empty_database(scratch_database_url, monkeypatch):
    # migrations/env.py reads its URL from get_settings(); leave the shared settings cache untouched.
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(database_url=scratch_database_url))
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    engine = sa.create_engine(scratch_database_url)
    try:
        command.upgrade(config, "head")
        command.check(config)  # the models and the migrated schema agree
        command.downgrade(config, "base")
        with engine.connect() as connection:
            leftover = sa.inspect(connection).get_table_names()
        assert leftover in ([], ["alembic_version"])
    finally:
        engine.dispose()


def _script_directory() -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    return ScriptDirectory.from_config(config)


def test_the_migration_chain_has_exactly_one_head():
    heads = _script_directory().get_heads()
    assert len(heads) == 1, f"migration branches, merge them: {sorted(heads)}"


def test_no_two_migration_files_claim_the_same_revision_id():
    revisions = [script.revision for script in _script_directory().walk_revisions()]
    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    assert len(list(versions.glob("2*.py"))) == len(revisions), "a migration file is orphaned or shares a revision id"
