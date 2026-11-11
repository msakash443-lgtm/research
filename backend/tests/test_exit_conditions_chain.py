"""X.177: exit-condition rules follow the stage they guard, so a chain that locks the analysis plan
(G8) before data collection (experimental) keeps the lock, collection and analysis checks."""

import uuid

from app import exit_conditions as ec
from app.database import SessionLocal
from app.models import GateCode, Project, ProjectStage
from tests.test_exit_conditions import _approve
from tests.test_research_types_api import _team_project

S = ProjectStage


def _experimental_project():
    world = _team_project()
    assert world["owner"].put(f"/api/projects/{world['project']}/research-type", json={"name": "experimental"}).status_code == 200
    return world["project"]


def _conditions_from(pid, stage, target):
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(pid))
        project.stage = stage
        db.commit()
        return {c.id: c for c in ec.conditions(db, project, target)}


def test_entering_plan_locked_needs_g8_on_an_experimental_chain_that_locks_before_collection():
    pid = _experimental_project()
    before = _conditions_from(pid, S.design_approved, S.plan_locked)["EC-11.1"]
    assert (before.met, before.level) == (False, ec.Level.required)
    _approve(pid, GateCode.G8)
    assert _conditions_from(pid, S.design_approved, S.plan_locked)["EC-11.1"].met is True


def test_leaving_data_collected_on_an_experimental_chain_still_lists_the_raw_data_check():
    pid = _experimental_project()
    found = _conditions_from(pid, S.data_collected, S.analyzed)
    assert found["EC-11.2"].met is False


def test_entering_analyzed_from_data_collected_on_an_experimental_chain_checks_g9_and_the_lock():
    pid = _experimental_project()
    found = _conditions_from(pid, S.data_collected, S.analyzed)
    assert {"EC-12.1", "EC-12.2", "EC-12.3"} <= found.keys()
    assert found["EC-12.1"].met is False
