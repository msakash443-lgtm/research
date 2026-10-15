import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app import research_types, stage_order
from app.database import SessionLocal
from app.gates import GATE_STAGES
from app.models import Project, User

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _migrator(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migrate.db').as_posix()}"
    # env.py reads the URL from get_settings(); leave the shared settings cache untouched.
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(database_url=url))
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    return config, sa.create_engine(url)


def _plans(engine) -> dict[str, tuple[str, dict]]:
    with engine.connect() as connection:
        rows = connection.execute(sa.text("SELECT id, research_type, stage_plan_json FROM projects")).all()
    return {id_: (name, json.loads(plan)) for id_, name, plan in rows}


def _assert_todays_full_journey(plan: dict) -> None:
    """The snapshot is a valid research type that reproduces today's 19 stages and their gates exactly."""
    research_type = research_types.ResearchType(**plan)
    research_types._check(research_type, "backfilled snapshot")
    assert (research_type.name, research_type.version) == ("full_journey", 1)
    assert research_type.chain == stage_order.chain()
    gate_of_stage = {stage: code for code, stage in GATE_STAGES.items()}
    assert [step.gate for step in research_type.stages] == [gate_of_stage.get(stage) for stage in stage_order.chain()]


def test_upgrade_backfills_every_existing_project_to_the_full_journey(tmp_path, monkeypatch):
    config, engine = _migrator(tmp_path, monkeypatch)
    command.upgrade(config, "20261015_0065")
    owner, first, second = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id, email) VALUES (:id, 'owner@example.test')"), {"id": owner})
        for project_id, stage in ((first, "idea"), (second, "screened")):
            connection.execute(
                sa.text("INSERT INTO projects (id, owner_id, title, stage) VALUES (:id, :owner, 'Existing', :stage)"),
                {"id": project_id, "owner": owner, "stage": stage},
            )

    command.upgrade(config, "20261015_0066")

    columns = {column["name"]: column for column in sa.inspect(engine).get_columns("projects")}
    assert columns["research_type"]["nullable"] is False
    assert columns["stage_plan_json"]["nullable"] is False
    plans = _plans(engine)
    assert set(plans) == {first, second}
    for name, plan in plans.values():
        assert name == "full_journey"
        _assert_todays_full_journey(plan)

    command.downgrade(config, "20261015_0065")
    columns = {column["name"] for column in sa.inspect(engine).get_columns("projects")}
    assert {"research_type", "stage_plan_json"}.isdisjoint(columns)
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM projects")).scalar() == 2

    command.upgrade(config, "20261015_0066")
    assert all(name == "full_journey" for name, _ in _plans(engine).values())
    engine.dispose()


def test_downgrade_refuses_while_a_project_is_on_another_research_type(tmp_path, monkeypatch):
    """Dropping the columns would silently put that project back on the full journey."""
    config, engine = _migrator(tmp_path, monkeypatch)
    command.upgrade(config, "20261015_0066")
    review = research_types.load_research_type("literature_review").model_dump(mode="json")
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id, email) VALUES ('u1', 'owner@example.test')"))
        connection.execute(sa.text(
            "INSERT INTO projects (id, owner_id, title, research_type, stage_plan_json) "
            "VALUES ('p1', 'u1', 'Review', 'literature_review', :plan)"), {"plan": json.dumps(review)})

    with pytest.raises(RuntimeError, match="research type other than full_journey"):
        command.downgrade(config, "20261015_0065")
    assert "research_type" in {column["name"] for column in sa.inspect(engine).get_columns("projects")}
    engine.dispose()


def test_a_new_project_gets_a_snapshot_of_the_research_type_it_names():
    with SessionLocal() as db:
        owner = User(email=f"type-{uuid.uuid4().hex}@example.test")
        db.add(owner)
        db.flush()
        default, review = Project(owner_id=owner.id, title="Default"), Project(
            owner_id=owner.id, title="Review", research_type="literature_review"
        )
        db.add_all([default, review])
        db.commit()

        assert default.research_type == "full_journey"
        assert default.stage_plan_json == research_types.load_research_type("full_journey").model_dump(mode="json")
        _assert_todays_full_journey(default.stage_plan_json)
        assert review.stage_plan_json == research_types.load_research_type("literature_review").model_dump(mode="json")


def test_a_project_naming_an_unknown_research_type_is_refused():
    with SessionLocal() as db:
        owner = User(email=f"type-{uuid.uuid4().hex}@example.test")
        db.add(owner)
        db.flush()
        db.add(Project(owner_id=owner.id, title="Unknown", research_type="no_such_type"))
        with pytest.raises(sa.exc.StatementError, match="does not exist"):
            db.flush()
