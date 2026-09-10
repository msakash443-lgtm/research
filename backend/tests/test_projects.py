import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Project, User


def create_user() -> User:
    user = User(id=uuid.uuid4(), email="researcher@example.com", display_name="Researcher")
    db = SessionLocal()
    db.add(user)
    db.commit()
    db.close()
    return user


def test_project_context_is_owned_and_persistent():
    user = create_user()
    client = TestClient(app)
    headers = {"X-User-Id": str(user.id)}

    created = client.post("/api/projects", headers=headers, json={"title": "Gender Wage Inequality"})
    assert created.status_code == 201
    project = created.json()

    context = client.post(
        f"/api/projects/{project['id']}/context",
        headers=headers,
        json={"kind": "question", "content": "How does gender affect wages in Kerala?"},
    )
    assert context.status_code == 201
    listed = client.get(f"/api/projects/{project['id']}/context", headers=headers)
    assert [item["content"] for item in listed.json()] == ["How does gender affect wages in Kerala?"]


def test_other_user_cannot_read_project():
    owner = create_user()
    other = User(id=uuid.uuid4(), email="other@example.com", display_name="Other")
    db = SessionLocal()
    project = Project(owner_id=owner.id, title="Private")
    db.add_all([other, project])
    db.commit()
    db.close()

    response = TestClient(app).get(f"/api/projects/{project.id}", headers={"X-User-Id": str(other.id)})
    assert response.status_code == 404
