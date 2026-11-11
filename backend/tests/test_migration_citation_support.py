import sqlalchemy as sa
from alembic import command

_CLAIM_GUARDS = {
    "claims_keep_evidence_delete",
    "claims_keep_evidence_move",
    "claims_require_evidence_insert",
    "claims_require_evidence_verify",
}


def _claim_guards(engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(sa.text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))
        return {name for (name,) in rows} & _CLAIM_GUARDS


def test_citation_support_downgrade_keeps_the_claim_guards(migrator):
    """Adding and dropping the support columns must leave every claim guard trigger in place."""
    config, engine = migrator

    command.upgrade(config, "20261013_0052")
    assert _claim_guards(engine) == _CLAIM_GUARDS

    command.downgrade(config, "20261013_0051")
    assert _claim_guards(engine) == _CLAIM_GUARDS
    columns = {column["name"] for column in sa.inspect(engine).get_columns("claim_evidence")}
    assert "support_confirmed_by" not in columns

    command.upgrade(config, "20261013_0052")
    assert _claim_guards(engine) == _CLAIM_GUARDS
