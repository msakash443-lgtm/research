"""X.162: `/ready` (and the start-up log) say plainly when the database is behind the code's
Alembic head, instead of letting the first request that touches a missing column 500 with no
explanation (reproduced live, X.160 walkthrough)."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.main import app
from app.migration_status import database_migration_status, revisions_behind, script_directory


def test_an_up_to_date_database_reports_nothing():
    script = script_directory()
    head = script.get_current_head()
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        c.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": head})
    assert database_migration_status(eng) is None


def test_a_database_with_no_alembic_version_table_reports_nothing():
    # A fresh dev/test database built by `AUTO_CREATE_SCHEMA`, not Alembic: not this check's job.
    eng = create_engine("sqlite:///:memory:")
    assert database_migration_status(eng) is None


def test_a_revision_id_the_code_does_not_know_reports_nothing_rather_than_guessing():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        c.execute(text("INSERT INTO alembic_version VALUES ('not_a_real_revision')"))
    assert database_migration_status(eng) is None


def test_a_database_several_migrations_behind_reports_the_exact_count():
    script = script_directory()
    head = script.get_current_head()
    # Three real steps back from head (asserted first so this test fails loudly, not silently,
    # if the chain is ever reshuffled near its tip).
    steps = list(script.walk_revisions(base="base", head=head))
    assert len(steps) >= 4
    three_back = steps[3].revision  # steps[0] is head; walk_revisions descends toward base
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        c.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": three_back})
    status = database_migration_status(eng)
    assert status is not None
    assert status == {
        "behind": 3,
        "db_revision": three_back,
        "code_revision": head,
        "message": "database is 3 migration(s) behind the code; run `alembic upgrade head`.",
    }


def test_revisions_behind_a_single_step_is_one_not_two():
    # Regression: `ScriptDirectory.walk_revisions(base, head)` includes `base` itself, so a naive
    # `len(...)` over-counts by one.
    script = script_directory()
    head = script.get_current_head()
    steps = list(script.walk_revisions(base="base", head=head))
    one_back = steps[1].revision
    assert revisions_behind(one_back, script) == 1


def test_ready_reports_the_migration_gap_when_the_database_is_behind(monkeypatch):
    from app import main as main_module

    script = script_directory()
    head = script.get_current_head()
    steps = list(script.walk_revisions(base="base", head=head))
    two_back = steps[2].revision
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with eng.begin() as c:
        c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        c.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": two_back})
    monkeypatch.setattr(main_module, "engine", eng)

    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"  # still ready: the probe query itself works
    assert body["database_migration"]["behind"] == 2
    assert "alembic upgrade head" in body["database_migration"]["message"]


def test_ready_says_nothing_extra_when_the_database_is_current():
    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ready", "database": "available"}
