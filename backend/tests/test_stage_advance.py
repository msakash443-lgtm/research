"""Plan M0.5.8: forward stage transitions. A gated stage is reached only after a person approved its gate."""

import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import stage_machine as sm
from app.artifacts import StageError
from app.database import SessionLocal
from app.main import app
from app.models import (
    Artifact, ArtifactStatus, AuditEvent, Gate, GateCode, GateStatus, Project, ProjectMember, ProjectRole,
    ProjectStage as S,
)

ORDER = [s.value for s in S]


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "A"}).json()["id"]
    return client, user_id


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner, owner_id = _login(f"adv-owner-{tag}@example.com")
    project_id = owner.post("/api/projects", json={"title": "Advance"}).json()["id"]
    clients = {"owner": owner}
    for role in (ProjectRole.supervisor, ProjectRole.co_author, ProjectRole.reviewer):
        client, uid = _login(f"adv-{role.value}-{tag}@example.com")
        with SessionLocal() as db:
            db.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=uuid.UUID(uid), role=role))
            db.commit()
        clients[role.value] = client
    return {"clients": clients, "project": project_id, "pid": uuid.UUID(project_id), "owner_id": owner_id}


def _set_stage(world, stage):
    with SessionLocal() as db:
        db.get(Project, world["pid"]).stage = stage
        db.commit()


def _stage(world):
    return world["clients"]["owner"].get(f"/api/projects/{world['project']}").json()["stage"]


def _advance(world, stage=None, who="owner"):
    body = {"stage": stage} if stage else {}
    return world["clients"][who].post(f"/api/projects/{world['project']}/stage/advance", json=body)


def _approve(world, code):
    return world["clients"]["owner"].post(f"/api/projects/{world['project']}/gates/{code}/approve", json={"note": "ok"})


def _events(world, action):
    with SessionLocal() as db:
        return [e for e in db.scalars(select(AuditEvent).where(AuditEvent.project_id == world["pid"], AuditEvent.action == action))]


# ---- the happy path --------------------------------------------------------------------------

def test_a_project_walks_the_whole_workflow_stopping_at_every_gate_until_a_person_approves(world):
    visited, gates_needed = ["idea"], []
    while visited[-1] != "submitted":
        response = _advance(world)
        if response.status_code == 409:
            detail = response.json()["detail"]
            assert detail["gate_status"] == "pending"
            gates_needed.append(detail["gate"])
            assert _approve(world, detail["gate"]).status_code == 200  # a person decides
            response = _advance(world)
        assert response.status_code == 200, response.text
        visited.append(response.json()["to_stage"])

    assert visited == ORDER[: ORDER.index("submitted") + 1]  # Appendix C, in order, nothing skipped
    assert gates_needed == [f"G{n}" for n in range(1, 12)]  # every one of the eleven gates stopped it
    assert _stage(world) == "submitted"


def test_a_blocked_advance_explains_which_gate_who_may_approve_it_and_changes_nothing(world):
    response = _advance(world)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["gate"] == "G1" and detail["gate_status"] == "pending" and detail["required_roles"] == ["owner", "supervisor"]
    assert "Gate G1 must be approved" in detail["message"] and "'scoped'" in detail["message"]
    assert _stage(world) == "idea" and _events(world, "project.advanced") == []


def test_an_owner_only_gate_names_only_the_owner(world):
    _set_stage(world, S.framework)

    detail = _advance(world).json()["detail"]

    assert detail["gate"] == "G7" and detail["required_roles"] == ["owner"]


def test_a_rejected_gate_does_not_let_the_project_through(world):
    world["clients"]["owner"].post(f"/api/projects/{world['project']}/gates/G1/reject", json={"note": "scope too wide"})

    detail = _advance(world).json()["detail"]

    assert detail["gate"] == "G1" and detail["gate_status"] == "rejected" and _stage(world) == "idea"


def test_stages_without_a_gate_need_no_approval(world):
    _set_stage(world, S.search_planned)

    response = _advance(world)

    assert response.status_code == 200
    assert response.json() == {"from_stage": "search_planned", "to_stage": "retrieved", "gate": None}


# ---- only one step, only forward ---------------------------------------------------------------

def test_stages_cannot_be_skipped_or_moved_backwards_with_advance(world):
    _set_stage(world, S.scoped)

    skip = _advance(world, "extracted")
    back = _advance(world, "idea")
    same = _advance(world, "scoped")

    for response in (skip, back, same):
        assert response.status_code == 409 and "does not follow 'scoped'" in response.json()["detail"]
    assert _stage(world) == "scoped"


def test_an_explicit_target_makes_a_retried_request_safe(world):
    _set_stage(world, S.search_planned)

    first = _advance(world, "retrieved")
    retry = _advance(world, "retrieved")  # e.g. the client timed out and asked again

    assert first.status_code == 200 and retry.status_code == 409
    assert _stage(world) == "retrieved"  # moved once, not twice


def test_after_submission_the_choice_must_be_made_and_each_branch_works(world):
    _set_stage(world, S.submitted)
    options = world["clients"]["owner"].get(f"/api/projects/{world['project']}/stage").json()["next"]
    assert [o["stage"] for o in options] == ["revision_loop", "accepted"]

    ambiguous = _advance(world)
    assert ambiguous.status_code == 409 and "Choose where to go next" in ambiguous.json()["detail"]
    assert _advance(world, "revision_loop").status_code == 200
    assert _advance(world, "submitted").status_code == 409  # resubmission is a re-entry, then forward again
    assert _advance(world, "accepted").status_code == 200


def test_accepted_is_final(world):
    _set_stage(world, S.accepted)

    response = _advance(world)

    assert response.status_code == 409 and "final stage" in response.json()["detail"]
    assert world["clients"]["owner"].get(f"/api/projects/{world['project']}/stage").json() == {"stage": "accepted", "next": []}


# ---- interaction with re-entry -----------------------------------------------------------------

def test_after_re_entry_the_voided_gate_must_be_approved_again_before_moving_on(world):
    owner = world["clients"]["owner"]
    _approve(world, "G1")
    _advance(world)  # idea -> scoped
    _approve(world, "G2")
    _advance(world)  # scoped -> search_planned
    assert _stage(world) == "search_planned"
    owner.post(f"/api/projects/{world['project']}/stage/reenter", json={"stage": "scoped", "reason": "scope changed"})

    blocked = _advance(world)
    assert blocked.status_code == 409 and blocked.json()["detail"]["gate"] == "G2"  # the earlier approval no longer counts
    _approve(world, "G2")
    assert _advance(world).status_code == 200 and _stage(world) == "search_planned"


# ---- who ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("who,allowed", [("owner", True), ("co_author", True), ("supervisor", False), ("reviewer", False)])
def test_only_owners_and_co_authors_can_advance(world, who, allowed):
    _set_stage(world, S.search_planned)

    response = _advance(world, who=who)

    assert response.status_code == (200 if allowed else 403)
    assert _stage(world) == ("retrieved" if allowed else "search_planned")


def test_every_member_can_see_where_the_project_can_go(world):
    _approve(world, "G1")

    for who in ("owner", "supervisor", "co_author", "reviewer"):
        body = world["clients"][who].get(f"/api/projects/{world['project']}/stage").json()
        assert body == {"stage": "idea", "next": [{"stage": "scoped", "gate": "G1", "gate_status": "approved", "ready": True}]}


def test_the_stage_view_shows_a_stage_that_is_not_ready(world):
    body = world["clients"]["owner"].get(f"/api/projects/{world['project']}/stage").json()

    assert body["next"] == [{"stage": "scoped", "gate": "G1", "gate_status": "pending", "ready": False}]


# ---- audit and side effects --------------------------------------------------------------------

def test_an_advance_is_audited_with_who_moved_it_and_who_approved_the_gate(world):
    _approve(world, "G1")
    _advance(world)

    (event,) = _events(world, "project.advanced")

    assert event.actor == world["owner_id"]
    assert event.payload_json == {"from": "idea", "to": "scoped", "gate": "G1", "gate_decided_by": world["owner_id"]}


def test_advancing_never_touches_gates_or_artifacts(world):
    _approve(world, "G1")
    with SessionLocal() as db:
        db.add(Artifact(project_id=world["pid"], kind="x", ref_id="", stage=S.scoped, status=ArtifactStatus.stale, stale_reason="r"))
        db.commit()
    before = _snapshot(world)

    _advance(world)

    assert _snapshot(world) == before


def _snapshot(world):
    with SessionLocal() as db:
        gates = sorted((g.code.value, g.status.value, g.decided_by, g.note) for g in db.scalars(select(Gate).where(Gate.project_id == world["pid"])))
        arts = sorted((a.kind, a.status.value, a.stale_reason) for a in db.scalars(select(Artifact).where(Artifact.project_id == world["pid"])))
    return gates, arts


# ---- system producers ----------------------------------------------------------------------------

def test_a_system_producer_can_advance_an_ungated_stage_but_can_never_pass_a_gate(world):
    _set_stage(world, S.search_planned)
    with SessionLocal() as db:
        project = db.get(Project, world["pid"])
        moved = sm.advance_stage(db, project, actor="agent:search")
        db.commit()
    assert moved.to_stage == S.retrieved

    with SessionLocal() as db:
        project = db.get(Project, world["pid"])
        with pytest.raises(sm.GateNotApproved) as blocked:
            sm.advance_stage(db, project, actor="agent:search")  # retrieved -> screened needs G3
        db.rollback()
    assert blocked.value.gate == GateCode.G3 and isinstance(blocked.value, StageError)
    assert _stage(world) == "retrieved"
    assert {g.status for g in _gates(world).values()} == {GateStatus.pending}  # the producer approved nothing
    (event,) = _events(world, "project.advanced")
    assert event.actor == "agent:search"


def _gates(world):
    with SessionLocal() as db:
        return {g.code: g for g in db.scalars(select(Gate).where(Gate.project_id == world["pid"]))}


def test_only_the_state_machine_and_re_entry_assign_a_projects_stage():
    """Every change of stage goes through a checked path (static guard)."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    setters = sorted(
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if re.search(r"\.stage\s*=[^=]", path.read_text(encoding="utf-8"))
    )
    assert setters == ["artifacts.py", "stage_machine.py"]
