import uuid

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.dependencies import APPROVE_ROLES, MANAGE_ROLES, READ_ROLES, WRITE_ROLES
from app.main import app
from app.models import ProjectMember, ProjectRole


def _login(email):
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": email, "display_name": "U"}).json()["id"]
    return client, user_id


@pytest.fixture
def project():
    owner, _ = _login("access-creator@example.com")
    project_id = owner.post("/api/projects", json={"title": "Shared"}).json()["id"]
    return owner, project_id


def _member(project_id, role):
    client, user_id = _login(f"access-{role.value}@example.com")
    with SessionLocal() as db:
        db.add(ProjectMember(project_id=uuid.UUID(project_id), user_id=uuid.UUID(user_id), role=role))
        db.commit()
    return client


def test_role_sets_match_the_agreed_proposal():
    assert READ_ROLES == set(ProjectRole)
    assert WRITE_ROLES == {ProjectRole.owner, ProjectRole.co_author}
    assert APPROVE_ROLES == {ProjectRole.owner, ProjectRole.supervisor}
    assert MANAGE_ROLES == {ProjectRole.owner}


@pytest.mark.parametrize("role", list(ProjectRole))
def test_every_role_can_read(project, role):
    _, project_id = project
    client = _member(project_id, role)

    assert client.get(f"/api/projects/{project_id}").status_code == 200
    assert client.get(f"/api/projects/{project_id}/sources").status_code == 200
    assert client.get(f"/api/projects/{project_id}/context").status_code == 200
    assert client.get(f"/api/projects/{project_id}/research-runs").status_code == 200
    assert project_id in [p["id"] for p in client.get("/api/projects").json()]


@pytest.mark.parametrize(
    "role,allowed", [(ProjectRole.owner, True), (ProjectRole.co_author, True), (ProjectRole.supervisor, False), (ProjectRole.reviewer, False)]
)
def test_only_owner_and_co_author_can_write(project, role, allowed):
    _, project_id = project
    client = _member(project_id, role)

    source = client.post(f"/api/projects/{project_id}/sources", json={"title": "S"})
    context = client.post(f"/api/projects/{project_id}/context", json={"kind": "question", "content": "Why?"})
    run = client.post(f"/api/projects/{project_id}/research-runs", json={"question": "A long enough question?"})

    expected = {"source": 201, "context": 201, "run": 202} if allowed else {"source": 403, "context": 403, "run": 403}
    assert {"source": source.status_code, "context": context.status_code, "run": run.status_code} == expected


def test_co_author_can_verify_but_reviewer_cannot(project):
    owner, project_id = project
    source_id = owner.post(f"/api/projects/{project_id}/sources", json={"title": "S"}).json()["id"]
    reviewer = _member(project_id, ProjectRole.reviewer)
    co_author = _member(project_id, ProjectRole.co_author)

    assert reviewer.post(f"/api/projects/{project_id}/sources/{source_id}/verify").status_code == 403
    assert co_author.post(f"/api/projects/{project_id}/sources/{source_id}/verify").status_code == 200


def test_non_members_get_404_not_403(project):
    _, project_id = project
    stranger, _ = _login("access-stranger@example.com")

    assert stranger.get(f"/api/projects/{project_id}").status_code == 404
    assert stranger.post(f"/api/projects/{project_id}/sources", json={"title": "S"}).status_code == 404
    assert stranger.get("/api/projects").json() == []


def test_owner_id_counts_as_owner_without_a_member_row(project):
    owner, project_id = project
    with SessionLocal() as db:
        db.query(ProjectMember).delete()
        db.commit()

    assert owner.post(f"/api/projects/{project_id}/sources", json={"title": "S"}).status_code == 201
