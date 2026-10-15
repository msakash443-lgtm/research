from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
_CLAIM_GUARDS = {
    "claims_keep_evidence_delete",
    "claims_keep_evidence_move",
    "claims_require_evidence_insert",
    "claims_require_evidence_verify",
}


def _claim_guards(engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(sa.text("SELECT name FROM sqlite_master WHERE type = 'trigger'"))
        return {name for (name,) in rows} & _CLAIM_GUARDS


def test_citation_support_downgrade_keeps_the_sqlite_claim_guards(tmp_path, monkeypatch):
    """SQLite's batch rebuild of claim_evidence fails while the claim guards reference it, and they must come back."""
    url = f"sqlite:///{(tmp_path / 'migrate.db').as_posix()}"
    # env.py reads the URL from get_settings(); leave the shared settings cache untouched.
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(database_url=url))
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    engine = sa.create_engine(url)

    command.upgrade(config, "20261013_0052")
    assert _claim_guards(engine) == _CLAIM_GUARDS

    command.downgrade(config, "20261013_0051")
    assert _claim_guards(engine) == _CLAIM_GUARDS
    columns = {column["name"] for column in sa.inspect(engine).get_columns("claim_evidence")}
    assert "support_confirmed_by" not in columns

    command.upgrade(config, "20261013_0052")
    assert _claim_guards(engine) == _CLAIM_GUARDS
    engine.dispose()
