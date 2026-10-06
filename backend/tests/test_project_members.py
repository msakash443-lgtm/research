import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.main import app
from app.models import Project, ProjectMember, ProjectRole, User


def test_creating_a_project_records_the_creator_as_owner():
    client = TestClient(app)
    user_id = client.post("/api/auth/development/login", json={"email": "m1@example.com", "display_name": "M"}).json()["id"]
    project_id = client.post("/api/projects", json={"title": "Members"}).json()["id"]

    with SessionLocal() as db:
        (member,) = db.scalars(select(ProjectMember).where(ProjectMember.project_id == uuid.UUID(project_id))).all()
        assert str(member.user_id) == user_id
        assert member.role == ProjectRole.owner


def _project_and_users(db):
    owner, other = User(email=f"o-{uuid.uuid4().hex}@x.test"), User(email=f"p-{uuid.uuid4().hex}@x.test")
    db.add_all([owner, other])
    db.flush()
    project = Project(owner_id=owner.id, title="P")
    db.add(project)
    db.flush()
    return project, other


def test_all_four_roles_can_be_stored():
    with SessionLocal() as db:
        project, _ = _project_and_users(db)
        for role in ProjectRole:
            user = User(email=f"{role.value}-{uuid.uuid4().hex}@x.test")
            db.add(user)
            db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
        db.commit()
        roles = {m.role for m in db.scalars(select(ProjectMember).where(ProjectMember.project_id == project.id))}
        assert roles == set(ProjectRole)


def test_a_user_has_one_role_per_project():
    with SessionLocal() as db:
        project, other = _project_and_users(db)
        db.add(ProjectMember(project_id=project.id, user_id=other.id, role=ProjectRole.reviewer))
        db.commit()
        db.add(ProjectMember(project_id=project.id, user_id=other.id, role=ProjectRole.supervisor))
        with pytest.raises(IntegrityError):
            db.commit()
