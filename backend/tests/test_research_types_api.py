import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app import research_types
from app.models import AuditEvent, Gate, GateCode, GateStatus, Project, ProjectMember, ProjectRole, ProjectStage


def _login(email: str):
    client = TestClient(app)
    response = client.post("/api/auth/development/login", json={"email": email, "display_name": email})
    return client, response.json()["id"]


def _project():
    tag = uuid.uuid4().hex[:8]
    owner, _ = _login(f"research-type-owner-{tag}@example.com")
    project_id = owner.post("/api/projects", json={"title": "Research type API"}).json()["id"]
    supervisor, supervisor_id = _login(f"research-type-supervisor-{tag}@example.com")
    coauthor, coauthor_id = _login(f"research-type-coauthor-{tag}@example.com")
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        project.members.append(ProjectMember(user_id=uuid.UUID(supervisor_id), role=ProjectRole.supervisor))
        project.members.append(ProjectMember(user_id=uuid.UUID(coauthor_id), role=ProjectRole.co_author))
        db.commit()
    return {"owner": owner, "supervisor": supervisor, "coauthor": coauthor, "project": project_id}


def test_research_type_catalogue_lists_valid_shipped_types():
    client, _ = _login(f"research-type-catalogue-{uuid.uuid4().hex[:8]}@example.com")
    response = client.get("/api/research-types")
    assert response.status_code == 200
    rows = {row["name"]: row for row in response.json()}
    assert {"full_journey", "literature_review", "qualitative"} <= rows.keys()
    assert rows["literature_review"]["stages"]
    assert all("stage" in step and "gate" in step for step in rows["literature_review"]["stages"])


def test_only_the_owner_can_change_a_project_research_type_at_idea():
    world = _project()
    assert world["supervisor"].put(f"/api/projects/{world['project']}/research-type", json={"name": "literature_review"}).status_code == 403
    assert world["coauthor"].put(f"/api/projects/{world['project']}/research-type", json={"name": "literature_review"}).status_code == 403
    response = world["owner"].put(f"/api/projects/{world['project']}/research-type", json={"name": "literature_review"})
    assert response.status_code == 200
    assert response.json()["name"] == "literature_review"
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["project"]))
        assert project.research_type == "literature_review"
        assert project.stage_plan_json["name"] == "literature_review"


def test_research_type_change_is_refused_after_stage_or_gate_progress():
    world = _project()
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["project"]))
        project.stage = ProjectStage.scoped
        db.commit()
    assert world["owner"].put(f"/api/projects/{world['project']}/research-type", json={"name": "qualitative"}).status_code == 409

    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["project"]))
        project.stage = ProjectStage.idea
        gate = db.scalar(select(Gate).where(Gate.project_id == project.id, Gate.code == GateCode.G1))
        gate.status = GateStatus.approved
        db.commit()
    assert world["owner"].put(f"/api/projects/{world['project']}/research-type", json={"name": "qualitative"}).status_code == 409


def test_stage_response_exposes_the_project_snapshot_with_labels_and_gates():
    world = _project()
    assert world["owner"].put(f"/api/projects/{world['project']}/research-type", json={"name": "literature_review"}).status_code == 200
    response = world["owner"].get(f"/api/projects/{world['project']}/stage")
    assert response.status_code == 200
    stages = response.json()["chain"]
    names = [step["stage"] for step in stages]
    assert "framework" not in names
    assert "plan_locked" not in names
    assert next(step for step in stages if step["stage"] == "design_approved")["gate"] == "G7"
    assert all("label" in step for step in stages)


def test_stage_response_exposes_whether_the_project_has_human_participants():
    world = _project()
    assert world["owner"].get(f"/api/projects/{world['project']}/stage").json()["human_participants"] is True
    assert world["owner"].put(f"/api/projects/{world['project']}/research-type", json={"name": "literature_review"}).status_code == 200
    assert world["owner"].get(f"/api/projects/{world['project']}/stage").json()["human_participants"] is False
    assert world["owner"].put(f"/api/projects/{world['project']}/research-type", json={"name": "qualitative"}).status_code == 200
    assert world["owner"].get(f"/api/projects/{world['project']}/stage").json()["human_participants"] is True


def _advance_to_g7(world, research_type: str):
    """A project on `research_type`, with every gate before G7 in its own order approved, so G7 can be decided."""
    assert world["owner"].put(f"/api/projects/{world['project']}/research-type", json={"name": research_type}).status_code == 200
    earlier = research_types.load_research_type(research_type).gates
    earlier = earlier[: earlier.index(GateCode.G7)]
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["project"]))
        project.stage = ProjectStage.scoped
        for code in earlier:
            gate = db.scalar(select(Gate).where(Gate.project_id == project.id, Gate.code == code))
            gate.status = GateStatus.approved
        db.commit()


def test_g7_for_a_no_participants_type_is_refused_without_the_confirmation_and_the_note_records_it():
    world = _project()
    _advance_to_g7(world, "literature_review")
    refused = world["owner"].post(f"/api/projects/{world['project']}/gates/G7/approve", json={})
    assert refused.status_code == 422
    assert "no human participants" in refused.json()["detail"].lower()
    with SessionLocal() as db:
        gate = db.scalar(
            select(Gate).where(Gate.project_id == uuid.UUID(world["project"]), Gate.code == GateCode.G7)
        )
        assert gate.status == GateStatus.pending
    approved = world["owner"].post(
        f"/api/projects/{world['project']}/gates/G7/approve", json={"confirm_no_human_participants": True}
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "approved"
    assert "no human participants" in body["note"].lower()


def test_g7_for_a_participants_type_needs_no_confirmation():
    world = _project()
    _advance_to_g7(world, "qualitative")
    approved = world["owner"].post(f"/api/projects/{world['project']}/gates/G7/approve", json={})
    assert approved.status_code == 200
    assert approved.json()["note"] is None


def test_literature_review_requires_its_own_gate_order_not_full_journeys():
    """X.43.7: literature_review puts G7 (design/ethics) right after G1, before G2 (unlike full_journey,
    where G7 comes much later) -- a real approve call, not just the pure gate_order() function, must
    enforce that order."""
    world = _project()
    assert world["owner"].put(
        f"/api/projects/{world['project']}/research-type", json={"name": "literature_review"}
    ).status_code == 200

    refused = world["owner"].post(f"/api/projects/{world['project']}/gates/G2/approve", json={"note": "too early"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["waiting_for"] == ["G1", "G7"]

    assert world["owner"].post(f"/api/projects/{world['project']}/gates/G1/approve", json={"note": "ok"}).status_code == 200

    # G1 is done, but G7 still stands between G1 and G2 for this type (unlike full_journey).
    refused = world["owner"].post(f"/api/projects/{world['project']}/gates/G2/approve", json={"note": "still too early"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["waiting_for"] == ["G7"]

    approved_g7 = world["owner"].post(
        f"/api/projects/{world['project']}/gates/G7/approve", json={"confirm_no_human_participants": True}
    )
    assert approved_g7.status_code == 200

    # Now G2 goes through, proving the type's own order -- not full_journey's -- is what's enforced.
    assert world["owner"].post(f"/api/projects/{world['project']}/gates/G2/approve", json={"note": "ok"}).status_code == 200


def test_reentry_resets_only_the_qualitative_chains_own_gates_leaving_the_dropped_g8_alone():
    """X.43.7: qualitative drops only G8 (plan_locked). Re-entering must reset the gates that are actually
    in its chain and never touch (or error on) G8's row, which still exists from project creation but is
    outside this project's chain."""
    world = _project()
    owner = world["owner"]
    assert owner.put(f"/api/projects/{world['project']}/research-type", json={"name": "qualitative"}).status_code == 200

    for code in ("G1", "G2", "G3", "G4", "G5", "G6"):
        assert owner.post(f"/api/projects/{world['project']}/gates/{code}/approve", json={"note": "ok"}).status_code == 200
    assert owner.post(f"/api/projects/{world['project']}/gates/G7/approve", json={}).status_code == 200  # qualitative has participants: no confirmation needed

    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["project"]))
        project.stage = ProjectStage.design_approved
        db.commit()

    reentered = owner.post(
        f"/api/projects/{world['project']}/stage/reenter",
        json={"stage": "search_planned", "reason": "Rethink the search"},
    )
    assert reentered.status_code == 200
    assert reentered.json()["gates_reset"] == ["G2", "G3", "G4", "G5", "G6", "G7"]

    with SessionLocal() as db:
        gates = {g.code: g for g in db.scalars(select(Gate).where(Gate.project_id == uuid.UUID(world["project"])))}
    assert gates[GateCode.G1].status == GateStatus.approved  # before the re-entry target: untouched
    for code in (GateCode.G2, GateCode.G3, GateCode.G4, GateCode.G5, GateCode.G6, GateCode.G7):
        assert gates[code].status == GateStatus.pending
    # G8 is not part of qualitative's chain: the reset must never touch it and never error on it either
    # (the 200 above already proves no error; this also proves the row itself is untouched).
    assert gates[GateCode.G8].status == GateStatus.pending
    assert gates[GateCode.G8].decided_by is None

    with SessionLocal() as db:
        reset_events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.project_id == uuid.UUID(world["project"]), AuditEvent.action == "gate.reset"
            )
        ).all()
    assert {e.payload_json["gate"] for e in reset_events} == {"G2", "G3", "G4", "G5", "G6", "G7"}



def test_stage_response_names_the_projects_research_type_so_the_picker_can_show_it():
    world = _project()
    assert world["owner"].get(f"/api/projects/{world['project']}/stage").json()["research_type"] == "full_journey"
    assert world["owner"].put(f"/api/projects/{world['project']}/research-type", json={"name": "qualitative"}).status_code == 200
    assert world["owner"].get(f"/api/projects/{world['project']}/stage").json()["research_type"] == "qualitative"
