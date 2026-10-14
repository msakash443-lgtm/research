import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.main import app
from app.models import Construct, Theory, TheoryConstruct


@pytest.fixture
def world():
    client = TestClient(app)
    client.post("/api/auth/development/login", json={"email": f"construct-{uuid.uuid4().hex}@example.com", "display_name": "C"})
    project = client.post("/api/projects", json={"title": "Construct inventory"})
    return client, project.json()["id"]


def test_constructs_store_author_definition_measures_and_jingle_jangle_flags(world):
    client, pid = world
    created = client.post(f"/api/projects/{pid}/constructs", json={
        "name": "Trust", "definition": "A willingness to accept vulnerability.",
        "measures": [{"scale": "Trust Index", "reliability": 0.91}], "author": "Smith 2020",
        "jingle_jangle_flags": ["same_label_different_definition"],
    })
    assert created.status_code == 201, created.text
    assert client.get(f"/api/projects/{pid}/constructs").json()[0]["measures"][0]["scale"] == "Trust Index"
    assert client.get(f"/api/projects/{pid}/constructs").json()[0]["jingle_jangle_flags"] == ["same_label_different_definition"]


def test_theory_can_reference_only_constructs_in_the_same_project(world):
    client, pid = world
    construct = client.post(f"/api/projects/{pid}/constructs", json={
        "name": "Trust", "definition": "Definition", "author": "Smith",
    }).json()
    created = client.post(f"/api/projects/{pid}/theories", json={
        "name": "Social exchange", "description": "A relational lens.", "construct_ids": [construct["id"]],
    })
    assert created.status_code == 201, created.text
    assert created.json()["construct_ids"] == [construct["id"]]
    foreign = client.post(f"/api/projects/{pid}/theories", json={
        "name": "Invalid", "description": "No", "construct_ids": [str(uuid.uuid4())],
    })
    assert foreign.status_code == 422


def test_construct_name_and_author_are_unique_per_project(world):
    client, pid = world
    body = {"name": "Trust", "definition": "Definition", "author": "Smith"}
    assert client.post(f"/api/projects/{pid}/constructs", json=body).status_code == 201
    assert client.post(f"/api/projects/{pid}/constructs", json=body).status_code == 409


def test_theory_construct_database_links_reject_cross_project_references(world):
    client, pid = world
    other = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    with SessionLocal() as db:
        construct = Construct(project_id=uuid.UUID(other), name="Foreign", definition="D", author="A", created_by="test")
        theory = Theory(project_id=uuid.UUID(pid), name="Theory", description="D", created_by="test")
        db.add_all([construct, theory])
        db.flush()
        db.add(TheoryConstruct(project_id=uuid.UUID(pid), theory_id=theory.id, construct_id=construct.id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_theory_construct_database_links_reject_cross_project_updates(world):
    client, pid = world
    other = client.post("/api/projects", json={"title": "Other"}).json()["id"]
    with SessionLocal() as db:
        construct = Construct(project_id=uuid.UUID(pid), name="Local", definition="D", author="A", created_by="test")
        theory = Theory(project_id=uuid.UUID(pid), name="Theory", description="D", created_by="test")
        db.add_all([construct, theory])
        db.flush()
        link = TheoryConstruct(project_id=uuid.UUID(pid), theory_id=theory.id, construct_id=construct.id)
        db.add(link)
        db.commit()
        link.project_id = uuid.UUID(other)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
