"""Research objectives are their own entries: amended with history, retired, and counted by EC-1.2 (X.193)."""
import uuid

import pytest

from app.database import SessionLocal
from app.exit_conditions import ec_1_2
from app.models import Project, ProjectMember, ProjectRole
from tests.api_helpers import create_project, sign_in

STATEMENT = "Map how remote work changed productivity measures"


@pytest.fixture
def world():
    tag = uuid.uuid4().hex[:8]
    owner = sign_in(f"owner-{tag}@example.com", "owner")
    pid = create_project(owner, "Objectives")
    reviewer = sign_in(f"reviewer-{tag}@example.com", "reviewer")
    reviewer_id = reviewer.get("/api/auth/me").json()["id"]
    with SessionLocal() as db:
        db.add(ProjectMember(project_id=uuid.UUID(pid), user_id=uuid.UUID(reviewer_id), role=ProjectRole.reviewer))
        db.commit()
    return {"owner": owner, "reviewer": reviewer, "pid": pid}


def _url(world, suffix=""):
    return f"/api/projects/{world['pid']}/objectives{suffix}"


def _add(world, **fields):
    response = world["owner"].post(_url(world), json={"statement": STATEMENT, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def test_an_objective_is_amended_with_its_earlier_wording_kept_in_history(world):
    objective = _add(world, measure="Self-rated output", target_date="2027-06-30")
    response = world["owner"].patch(_url(world, f"/{objective['id']}"), json={"statement": "Map remote-work productivity"})
    assert response.status_code == 200, response.text
    amended = response.json()
    assert amended["statement"] == "Map remote-work productivity"
    assert amended["measure"] == "Self-rated output"
    assert [entry["statement"] for entry in amended["history"]] == [STATEMENT]


def test_a_retired_objective_is_hidden_from_the_list_but_never_deleted(world):
    objective = _add(world)
    assert world["owner"].post(_url(world, f"/{objective['id']}/retire")).status_code == 200
    assert world["owner"].get(_url(world)).json() == []
    kept = world["owner"].get(_url(world), params={"include_retired": "true"}).json()
    assert [item["id"] for item in kept] == [objective["id"]]
    assert world["owner"].patch(_url(world, f"/{objective['id']}"), json={"statement": "Too late"}).status_code == 409


def test_an_objective_cannot_point_at_a_research_question_from_another_project(world):
    other_owner = sign_in(f"other-{uuid.uuid4().hex[:8]}@example.com", "other")
    other_pid = create_project(other_owner, "Elsewhere")
    question = other_owner.post(
        f"/api/projects/{other_pid}/research-questions", json={"text": "A question in another project", "type": "descriptive"}
    )
    assert question.status_code == 201, question.text
    response = world["owner"].post(_url(world), json={"statement": STATEMENT, "research_question_id": question.json()["id"]})
    assert response.status_code == 422


def test_ec_1_2_is_met_by_a_research_objective_alone(world):
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["pid"]))
        assert ec_1_2(db, project).met is False
    _add(world)
    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(world["pid"]))
        assert ec_1_2(db, project).met is True
