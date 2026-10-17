import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Gate, GateCode, GateStatus, Project, ProjectMember, ProjectRole, ProjectStage


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
