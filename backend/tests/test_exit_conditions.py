import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import exit_conditions as ec
from app import stage_order
from app.database import SessionLocal
from app.gates import ensure_gates
from app.main import app
from app.models import Gate, GateCode, GateStatus, Project, ProjectStage


def _owner_project():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": "ec-owner@example.com", "display_name": "O"})
    pid = client.post("/api/projects", json={"title": "Exit conditions"}).json()["id"]
    return client, pid


def _conditions(pid, target=ProjectStage.scoped, stage=None):
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(pid))
        if stage is not None:
            project.stage = stage
            db.commit()
        return ec.conditions(db, project, target)


def _by_id(found):
    return {c.id: c for c in found}


def _approve(pid, code):
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(pid))
        ensure_gates(db, project)
        gate = db.scalar(select(Gate).where(Gate.project_id == project.id, Gate.code == code))
        gate.status = GateStatus.approved
        db.commit()


def test_the_gate_row_is_unmet_with_a_fix_until_a_person_approves_that_gate():
    _, pid = _owner_project()
    before = _by_id(_conditions(pid, ProjectStage.scoped))["EC-1.1"]
    assert (before.met, before.level) == (False, ec.Level.required)
    assert before.fix is not None
    _approve(pid, GateCode.G1)
    after = _by_id(_conditions(pid, ProjectStage.scoped))["EC-1.1"]
    assert after.met is True and after.fix is None


def test_a_target_is_blocked_while_an_earlier_gate_is_unapproved():
    _, pid = _owner_project()
    assert _by_id(_conditions(pid, ProjectStage.search_planned))["EC-0.3"].met is False
    _approve(pid, GateCode.G1)
    assert _by_id(_conditions(pid, ProjectStage.search_planned))["EC-0.3"].met is True


def test_no_row_that_needs_an_unbuilt_task_ever_reports_met():
    _, pid = _owner_project()
    for leave in ProjectStage:
        for target in stage_order.chain():
            for condition in _conditions(pid, target, stage=leave):
                if condition.message.startswith("not built yet"):
                    assert condition.met is False, condition.id
                    assert condition.level == ec.Level.information, condition.id
                    assert condition.fix is None, condition.id


def test_a_question_or_objective_is_needed_for_ec_1_2():
    client, pid = _owner_project()
    assert _by_id(_conditions(pid))["EC-1.2"].met is False
    client.post(f"/api/projects/{pid}/context", json={"kind": "question", "content": "Does remote work raise output?"})
    assert _by_id(_conditions(pid))["EC-1.2"].met is True


def test_ec_3_2_is_met_without_seeds_and_stays_unmet_until_recall_is_measured():
    client, pid = _owner_project()
    search_done = ProjectStage.search_planned
    assert _by_id(_conditions(pid, ProjectStage.retrieved, stage=search_done))["EC-3.2"].met is True
    client.post(f"/api/projects/{pid}/seeds", json={"title": "A known paper"})
    unmeasured = _by_id(_conditions(pid, ProjectStage.retrieved, stage=search_done))["EC-3.2"]
    assert unmeasured.met is False
    assert unmeasured.fix is not None


def test_a_record_with_no_decision_is_unscreened_for_ec_4_3():
    client, pid = _owner_project()
    client.post(f"/api/projects/{pid}/sources", json={"title": "A paper", "evidence_excerpt": "text"})
    assert _by_id(_conditions(pid, ProjectStage.screened, stage=ProjectStage.retrieved))["EC-4.3"].met is False
